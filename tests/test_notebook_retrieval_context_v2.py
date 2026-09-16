from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.notebook import (
    NOTEBOOK_GOLDEN_ACL_REF,
    NOTEBOOK_GOLDEN_GENERATION_ID,
    NOTEBOOK_GOLDEN_PROJECT_ID,
    NotebookAdapterV2,
    NotebookCellChangeType,
    NotebookCellMatchMethod,
    NotebookChannel,
    NotebookComparisonSelectionError,
    NotebookComparisonSpecV2,
    NotebookContextAvailability,
    NotebookContextBuilderV2,
    NotebookFilterError,
    NotebookPublicationError,
    NotebookPublicationSelectorV2,
    NotebookRerankFeatures,
    NotebookRetrieverV2,
    NotebookSearchFiltersV2,
    NotebookSearchScope,
    NotebookV2Store,
    build_notebook_fixture_v1,
    compare_notebook_publications_v2,
    rerank_notebook_candidate_v2,
    select_notebook_comparison_v2,
)


def _published_store(tmp_path: Path) -> tuple[NotebookV2Store, object]:
    fixture = build_notebook_fixture_v1()
    store = NotebookV2Store(tmp_path / "notebook-v2.sqlite3", isolated_root=tmp_path)
    store.initialize()
    for publication in fixture.publications:
        store.publish(publication)
    return store, fixture


def _scope(**updates: object) -> NotebookSearchScope:
    values: dict[str, object] = {
        "project_id": NOTEBOOK_GOLDEN_PROJECT_ID,
        "allowed_acl_refs": (NOTEBOOK_GOLDEN_ACL_REF,),
        "generation_id": NOTEBOOK_GOLDEN_GENERATION_ID,
    }
    values.update(updates)
    return NotebookSearchScope(**values)


def test_notebook_retriever_uses_independent_channels_and_acl(tmp_path: Path) -> None:
    store, _ = _published_store(tmp_path)
    retriever = NotebookRetrieverV2(store)
    first = retriever.search(
        scope=_scope(),
        query="Retrieve the RuntimeError evidence from the failed retry.",
    )
    second = retriever.search(
        scope=_scope(),
        query="Retrieve the RuntimeError evidence from the failed retry.",
    )
    assert first == second
    assert first.candidates
    assert any(item.unit_type == "error" for item in first.candidates)
    assert first.trace.reasoning_included is False
    assert first.trace.query_sha256.startswith("sha256:")
    assert "RuntimeError evidence" not in first.model_dump_json()
    channels = {item.channel: item for item in first.trace.channels}
    assert channels[NotebookChannel.EXACT].candidate_count > 0
    assert channels[NotebookChannel.STRUCTURED].candidate_count > 0
    assert channels[NotebookChannel.SPARSE].candidate_count > 0
    assert channels[NotebookChannel.DENSE].status == "UNAVAILABLE"
    assert channels[NotebookChannel.DENSE].candidate_count == 0

    denied = retriever.search(
        scope=_scope(allowed_acl_refs=("private-other",)),
        query="RuntimeError failed retry",
    )
    assert denied.candidates == ()
    assert denied.trace.publication_count == 0
    with pytest.raises(ValidationError, match="non-empty ACL"):
        _scope(allowed_acl_refs=())


def test_notebook_filters_run_before_rank_and_fail_on_ambiguous_cell(
    tmp_path: Path,
) -> None:
    store, fixture = _published_store(tmp_path)
    selected_publication = fixture.publications[1]
    result = NotebookRetrieverV2(store).search(
        scope=_scope(),
        query="threshold parameter",
        filters=NotebookSearchFiltersV2(
            revision_ids=(selected_publication.revision.revision_id,),
            execution_ids=(selected_publication.execution.execution_id,),
            parameter_names=("threshold",),
        ),
    )
    assert result.candidates
    assert all(item.unit_type == "parameter" for item in result.candidates)
    assert all(
        item.revision_id == selected_publication.revision.revision_id
        and item.execution_id == selected_publication.execution.execution_id
        for item in result.candidates
    )
    trace = result.trace.selection_trace
    assert trace.filter_before_rank is True
    assert trace.publication_count_after_filter == 1
    assert trace.unit_count_after_filter < trace.unit_count_before_filter

    failed_publication = fixture.publications[0]
    error_artifact = next(
        item for item in failed_publication.artifacts if str(item.artifact_type) == "error"
    )
    output = NotebookRetrieverV2(store).search(
        scope=_scope(),
        query="RuntimeError output",
        filters=NotebookSearchFiltersV2(
            execution_ids=(failed_publication.execution.execution_id,),
            output_ids=(error_artifact.artifact_id,),
            output_types=("error",),
        ),
    )
    assert output.candidates
    assert {item.entity_id for item in output.candidates} == {error_artifact.artifact_id}
    assert {item.unit_type for item in output.candidates} == {"error"}

    shared_native_id = selected_publication.cell_versions[0].native_cell_id
    assert shared_native_id is not None
    with pytest.raises(NotebookFilterError, match="notebook_cell_identity_ambiguous"):
        NotebookRetrieverV2(store).search(
            scope=_scope(),
            query="cell",
            filters=NotebookSearchFiltersV2(cell_ids=(shared_native_id,)),
        )


def test_notebook_reranker_penalizes_weak_evidence_but_keeps_failure_tasks() -> None:
    common = {
        "exact_match": True,
        "token_overlap": 1.0,
        "task_match": True,
        "task_priority": 1.0,
        "metadata_overlap": 0.0,
        "graph_distance": None,
        "current_revision": True,
        "unexecuted": False,
        "markdown_only": False,
        "metric_unconfirmed": False,
    }
    fresh = rerank_notebook_candidate_v2(
        NotebookRerankFeatures(**common, stale=False, task="output")
    )
    stale_output = rerank_notebook_candidate_v2(
        NotebookRerankFeatures(**common, stale=True, task="output")
    )
    stale_error = rerank_notebook_candidate_v2(
        NotebookRerankFeatures(**common, stale=True, task="error")
    )
    markdown = rerank_notebook_candidate_v2(
        NotebookRerankFeatures(
            **{**common, "markdown_only": True},
            stale=False,
            task="code",
        )
    )
    unconfirmed = rerank_notebook_candidate_v2(
        NotebookRerankFeatures(
            **{**common, "metric_unconfirmed": True},
            stale=False,
            task="output",
        )
    )
    assert stale_output.score < fresh.score
    assert stale_error.score > stale_output.score
    assert markdown.score < fresh.score
    assert unconfirmed.score < fresh.score
    assert "metric-unconfirmed" in unconfirmed.reasons


def test_notebook_cell_matcher_detects_move_output_change_and_insertion() -> None:
    fixture = build_notebook_fixture_v1()
    retry = fixture.publications[1]
    moved = fixture.publications[2]
    comparison = compare_notebook_publications_v2(retry, moved)
    assert comparison.as_contract(retry).matches_sha256 == comparison.matches_sha256
    assert comparison.ambiguous_pair_count == 0
    assert any(item.change_types == (NotebookCellChangeType.ADDED,) for item in comparison.matches)
    assert any(
        NotebookCellChangeType.MOVED in item.change_types
        for item in comparison.matches
        if item.method == NotebookCellMatchMethod.NATIVE_ID
    )
    assert any(
        NotebookCellChangeType.OUTPUT_CHANGED in item.change_types for item in comparison.matches
    )
    assert all(
        item.method != NotebookCellMatchMethod.ORDINAL_FALLBACK for item in comparison.matches
    )

    same_revision_retry = compare_notebook_publications_v2(
        fixture.publications[0],
        retry,
    )
    assert same_revision_retry.baseline_revision_id == same_revision_retry.candidate_revision_id
    assert any(
        NotebookCellChangeType.REEXECUTED_SAME_SOURCE in item.change_types
        for item in same_revision_retry.matches
    )


def test_notebook_cell_matcher_fails_closed_on_duplicate_derived_cells() -> None:
    adapter = NotebookAdapterV2()

    def payload(cells: list[dict[str, object]]) -> bytes:
        return json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {"language_info": {"name": "python"}},
                "cells": cells,
            },
            separators=(",", ":"),
        ).encode()

    repeated = {
        "cell_type": "code",
        "metadata": {},
        "source": "value = 1",
        "execution_count": 1,
        "outputs": [],
    }
    baseline = adapter.build_publication(
        payload=payload([repeated, repeated]),
        project_id="project-notebook-ambiguous",
        acl_ref="public",
        generation_id="nbgen-ambiguous",
        source_key="ambiguous.ipynb",
        source_version="revision-1",
        execution_key="execution-1",
    )
    candidate = adapter.build_publication(
        payload=payload(
            [
                {
                    "cell_type": "markdown",
                    "metadata": {},
                    "source": "inserted one",
                },
                {
                    "cell_type": "markdown",
                    "metadata": {},
                    "source": "inserted two",
                },
                repeated,
                repeated,
            ]
        ),
        project_id="project-notebook-ambiguous",
        acl_ref="public",
        generation_id="nbgen-ambiguous",
        source_key="ambiguous.ipynb",
        source_version="revision-2",
        execution_key="execution-2",
    )
    comparison = compare_notebook_publications_v2(baseline, candidate)
    assert comparison.ambiguous_pair_count == 2
    assert sum(item.method == NotebookCellMatchMethod.UNMATCHED for item in comparison.matches) == 6


def test_notebook_comparison_selection_is_explicit_and_version_bound() -> None:
    fixture = build_notebook_fixture_v1()
    baseline = fixture.publications[1]
    candidate = fixture.publications[2]
    spec = NotebookComparisonSpecV2(
        baseline=NotebookPublicationSelectorV2(
            template_id=baseline.template.template_id,
            revision_id=baseline.revision.revision_id,
            execution_id=baseline.execution.execution_id,
        ),
        candidate=NotebookPublicationSelectorV2(
            template_id=candidate.template.template_id,
            revision_id=candidate.revision.revision_id,
            execution_id=candidate.execution.execution_id,
        ),
    )
    selected_baseline, selected_candidate, trace = select_notebook_comparison_v2(
        fixture.publications,
        spec,
    )
    assert selected_baseline == baseline
    assert selected_candidate == candidate
    assert trace.explicit is True

    wrong = spec.model_copy(
        update={
            "candidate": spec.candidate.model_copy(
                update={"execution_id": baseline.execution.execution_id}
            )
        }
    )
    with pytest.raises(
        NotebookComparisonSelectionError,
        match="comparison_version_mismatch",
    ):
        select_notebook_comparison_v2(fixture.publications, wrong)


def test_notebook_context_is_task_specific_bounded_and_citation_stable(
    tmp_path: Path,
) -> None:
    store, fixture = _published_store(tmp_path)
    scope = _scope()
    comparison = compare_notebook_publications_v2(
        fixture.publications[1],
        fixture.publications[2],
    )
    builder = NotebookContextBuilderV2(store)
    first = builder.build(
        scope=scope,
        query="Compare the two Notebook revisions and moved cells.",
        comparison=comparison,
        max_chars=2_000,
    )
    second = builder.build(
        scope=scope,
        query="Compare the two Notebook revisions and moved cells.",
        comparison=comparison,
        max_chars=2_000,
    )
    assert first == second
    assert first.availability == NotebookContextAvailability.AVAILABLE
    assert first.missing_evidence == ()
    assert first.blocks[0].section == "comparison_summary"
    assert first.metrics.used_chars <= first.metrics.budget_chars
    assert first.metrics.citation_count == first.metrics.selected_blocks
    assert {item.citation_id for item in first.citations} == {
        item.citation_id for item in first.blocks
    }
    assert first.reasoning_included is False
    rendered = first.model_dump_json()
    assert "/Users/" not in rendered
    assert "\\\\server\\share" not in rendered

    tiny = builder.build(
        scope=scope,
        query="Compare revisions.",
        comparison=comparison,
        max_chars=256,
    )
    assert tiny.metrics.used_chars <= 256
    assert tiny.metrics.truncated_blocks in {0, 1}

    no_match = builder.build(
        scope=_scope(generation_id="nbgen-missing"),
        query="Retrieve RuntimeError evidence.",
        max_chars=1_000,
    )
    assert no_match.availability == NotebookContextAvailability.UNAVAILABLE
    assert no_match.blocks == ()
    assert no_match.missing_evidence

    with pytest.raises(ValueError, match="outside governed"):
        builder.build(
            scope=_scope(allowed_acl_refs=("private-other",)),
            query="Compare revisions.",
            comparison=comparison,
        )


def test_notebook_generation_tombstone_hides_without_deleting_history(
    tmp_path: Path,
) -> None:
    payload = json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {"language_info": {"name": "python"}},
            "cells": [
                {
                    "id": "value",
                    "cell_type": "code",
                    "metadata": {},
                    "source": "value = 1",
                    "execution_count": 1,
                    "outputs": [],
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    adapter = NotebookAdapterV2()
    first = adapter.build_publication(
        payload=payload,
        project_id="project-notebook-generation",
        acl_ref="public",
        generation_id="nbgen-one",
        source_key="generation.ipynb",
        source_version="revision-1",
        execution_key="execution-1",
    )
    second = adapter.build_publication(
        payload=payload,
        project_id="project-notebook-generation",
        acl_ref="public",
        generation_id="nbgen-two",
        source_key="generation.ipynb",
        source_version="revision-1",
        execution_key="execution-1",
    )
    assert first.template.template_id == second.template.template_id
    assert first.revision.revision_id != second.revision.revision_id
    assert first.execution.execution_id != second.execution.execution_id

    store = NotebookV2Store(tmp_path / "generation.sqlite3", isolated_root=tmp_path)
    store.initialize()
    store.publish(first)
    store.publish(second)
    disposition = store.tombstone_template(
        project_id=first.template.scope.project_id,
        acl_ref=first.template.scope.acl_ref,
        generation_id=first.template.scope.generation_id,
        template_id=first.template.template_id,
        source_key=first.template.source_key,
        reason="source-deleted",
    )
    repeated = store.tombstone_template(
        project_id=first.template.scope.project_id,
        acl_ref=first.template.scope.acl_ref,
        generation_id=first.template.scope.generation_id,
        template_id=first.template.template_id,
        source_key=first.template.source_key,
        reason="source-deleted",
    )
    assert repeated["tombstone_id"] == disposition["tombstone_id"]
    assert (
        store.list_publications(
            project_id=first.template.scope.project_id,
            allowed_acl_refs=("public",),
            generation_id="nbgen-one",
        )
        == ()
    )
    assert store.list_publications(
        project_id=second.template.scope.project_id,
        allowed_acl_refs=("public",),
        generation_id="nbgen-two",
    ) == (second,)
    assert store.get_publication(first.publication_id) == first

    later_same_generation = adapter.build_publication(
        payload=payload,
        project_id="project-notebook-generation",
        acl_ref="public",
        generation_id="nbgen-one",
        source_key="generation.ipynb",
        source_version="revision-1",
        execution_key="execution-later",
    )
    with pytest.raises(NotebookPublicationError, match="tombstoned"):
        store.publish(later_same_generation)
    with pytest.raises(NotebookPublicationError, match="authority"):
        store.tombstone_template(
            project_id=first.template.scope.project_id,
            acl_ref="private",
            generation_id=first.template.scope.generation_id,
            template_id=first.template.template_id,
            source_key=first.template.source_key,
            reason="source-deleted",
        )
