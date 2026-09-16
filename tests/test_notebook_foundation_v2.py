from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.notebook import (
    EXPECTED_NOTEBOOK_SLICE_COUNTS,
    NOTEBOOK_GOLDEN_AUTHORITY_SHA256,
    NOTEBOOK_GOLDEN_CASE_COUNT,
    NOTEBOOK_GOLDEN_PACKAGE_SHA256,
    NOTEBOOK_V2_TABLES,
    NotebookGoldenError,
    NotebookMetric,
    NotebookMetricStatus,
    NotebookPublication,
    NotebookReviewAvailability,
    NotebookReviewedCandidate,
    NotebookReviewedRow,
    NotebookStorePathError,
    NotebookV2Store,
    build_notebook_fixture_v1,
    evaluate_reviewed_notebook_retrieval,
    load_notebook_golden_v1,
    perfect_reviewed_rows_v1,
    probe_notebook_foundation_v1,
)


def test_notebook_golden_v1_is_exact_content_addressed_truth() -> None:
    released = load_notebook_golden_v1()
    assert released.package_sha256 == NOTEBOOK_GOLDEN_PACKAGE_SHA256
    assert released.authority_sha256 == NOTEBOOK_GOLDEN_AUTHORITY_SHA256
    assert len(released.cases) == NOTEBOOK_GOLDEN_CASE_COUNT == 40
    assert tuple(case.case_id for case in released.cases) == tuple(
        f"nb-v1-{index:03d}" for index in range(1, 41)
    )
    assert Counter(case.slice for case in released.cases) == Counter(EXPECTED_NOTEBOOK_SLICE_COUNTS)
    assert [EXPECTED_NOTEBOOK_SLICE_COUNTS[key] for key in EXPECTED_NOTEBOOK_SLICE_COUNTS] == [
        5,
        6,
        5,
        6,
        6,
        5,
        4,
        3,
    ]
    assert len(released.entities) == 100
    assert len({item.entity_id for item in released.entities}) == 100
    assert sum(bool(case.hard_negative_entity_ids) for case in released.cases) == 19

    with pytest.raises((ValidationError, ValueError)):
        released.model_copy(update={"package_sha256": "sha256:" + ("0" * 64)})
    first = released.cases[0]
    with pytest.raises((ValidationError, ValueError)):
        first.model_copy(update={"expected_entity_ids": ()})


def test_notebook_fixture_uses_real_adapter_and_separates_revision_execution() -> None:
    fixture = build_notebook_fixture_v1()
    failed, retry, moved, distractor = fixture.publications
    assert failed.revision.revision_id == retry.revision.revision_id
    assert failed.execution.execution_id != retry.execution.execution_id
    assert failed.execution.status == "failed"
    assert retry.execution.status == "completed"
    assert moved.revision.revision_id != retry.revision.revision_id
    assert distractor.template.template_id != retry.template.template_id

    retry_cells = {item.native_cell_id: item for item in retry.cell_versions}
    moved_cells = {item.native_cell_id: item for item in moved.cell_versions}
    assert retry_cells["score"].stable_cell_id == moved_cells["score"].stable_cell_id
    assert retry_cells["score"].cell_version_id != moved_cells["score"].cell_version_id
    assert retry_cells["score"].display_order != moved_cells["score"].display_order

    parameters = {item.name: item for item in retry.parameters}
    assert parameters["seed"].value_type == "integer"
    assert parameters["seed"].canonical_value == "7"
    assert parameters["threshold"].value_type == "float"
    assert parameters["dataset"].value_type == "string"
    rendered = "\n".join(item.model_dump_json() for item in fixture.publications)
    assert "/Users/example/private" not in rendered
    assert "not-a-real-image" not in rendered
    assert "<script>" not in rendered
    assert "steal()" not in rendered
    assert "[REDACTED_PATH]" in rendered
    assert any(item.binary_omitted for item in retry.artifacts)

    tampered = retry.model_dump(mode="python", round_trip=True)
    tampered["artifacts"][0]["text"] = "fabricated"
    with pytest.raises(ValidationError, match="publication id"):
        NotebookPublication.model_validate(tampered)


def test_notebook_isolated_store_publishes_atomically_and_idempotently(tmp_path: Path) -> None:
    fixture = build_notebook_fixture_v1()
    database = tmp_path / "store" / "notebook-v2.sqlite3"
    store = NotebookV2Store(database, isolated_root=tmp_path)
    store.initialize()
    results = [store.publish(item) for item in fixture.publications]
    assert [item["disposition"] for item in results] == ["published"] * 4
    assert all(item["database_sidecars"] == () for item in results)
    assert store.publish(fixture.publications[0])["disposition"] == "already_published"
    counts = store.table_counts()
    assert set(counts) == set(NOTEBOOK_V2_TABLES)
    assert counts["notebook_templates_v2"] == 2
    assert counts["notebook_revisions"] == 3
    assert counts["notebook_executions"] == 4
    assert counts["notebook_cell_versions"] == 26
    assert counts["notebook_cell_executions"] == 37
    assert counts["notebook_parameters"] == 11
    assert counts["notebook_artifacts"] == 19
    assert counts["notebook_publications_v2"] == 4
    loaded = store.get_publication(fixture.publications[1].publication_id)
    assert loaded == fixture.publications[1]
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(database) + "-shm").exists()

    rollback_database = tmp_path / "rollback" / "notebook-v2.sqlite3"
    rollback = NotebookV2Store(rollback_database, isolated_root=tmp_path)
    rollback.initialize()
    with sqlite3.connect(rollback_database) as db:
        db.executescript(
            """
            CREATE TRIGGER reject_notebook_artifact
            BEFORE INSERT ON notebook_artifacts
            BEGIN
              SELECT RAISE(ABORT, 'injected artifact failure');
            END;
            """
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected artifact failure"):
        rollback.publish(fixture.publications[0])
    assert all(value == 0 for value in rollback.table_counts().values())


def test_notebook_store_rejects_formal_escape_and_symlink_paths(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    with pytest.raises(NotebookStorePathError):
        NotebookV2Store(outside / "db.sqlite3", isolated_root=tmp_path)
    with pytest.raises(NotebookStorePathError, match="formal"):
        NotebookV2Store(tmp_path / "evidence-rag.sqlite3", isolated_root=tmp_path)

    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(NotebookStorePathError, match="symlink"):
        NotebookV2Store(linked / "db.sqlite3", isolated_root=tmp_path)


def test_notebook_evaluator_recomputes_truth_and_freezes_denominators() -> None:
    released = load_notebook_golden_v1()
    rows = perfect_reviewed_rows_v1(released)
    report = evaluate_reviewed_notebook_retrieval(rows, dataset=released)
    assert report.case_count == 40
    assert report.reviewed_row_count == 40
    assert report.hard_negative_case_count == 19
    assert all(item.status == NotebookMetricStatus.AVAILABLE for item in report.metrics)
    assert all(item.numerator == item.eligible_denominator for item in report.metrics)
    assert all(item.value == 1.0 for item in report.metrics)
    assert [item.eligible_denominator for item in report.slices] == [5, 6, 5, 6, 6, 5, 4, 3]

    with pytest.raises(NotebookGoldenError, match="membership"):
        evaluate_reviewed_notebook_retrieval(rows, dataset=released, case_membership=())
    with pytest.raises(NotebookGoldenError, match="40"):
        evaluate_reviewed_notebook_retrieval(rows[:-1], dataset=released)
    with pytest.raises(NotebookGoldenError, match="order"):
        evaluate_reviewed_notebook_retrieval((rows[1], rows[0], *rows[2:]), dataset=released)

    first = rows[0]
    bad_locator = first.model_copy(
        update={
            "candidates": (
                NotebookReviewedCandidate(
                    entity_id=first.candidates[0].entity_id,
                    locator="notebook://project-notebook-golden-v1/tampered",
                ),
            )
        }
    )
    with pytest.raises(NotebookGoldenError, match="locator"):
        evaluate_reviewed_notebook_retrieval((bad_locator, *rows[1:]), dataset=released)

    unknown = first.model_copy(
        update={
            "candidates": (
                NotebookReviewedCandidate(
                    entity_id="fabricated-entity",
                    locator=first.candidates[0].locator,
                ),
            )
        }
    )
    with pytest.raises(NotebookGoldenError, match="authority"):
        evaluate_reviewed_notebook_retrieval((unknown, *rows[1:]), dataset=released)

    unavailable = first.model_copy(
        update={
            "availability": NotebookReviewAvailability.UNAVAILABLE,
            "candidates": (),
            "diagnostic": "source unavailable",
        }
    )
    partial = evaluate_reviewed_notebook_retrieval((unavailable, *rows[1:]), dataset=released)
    retrieval = next(
        item for item in partial.metrics if item.metric == NotebookMetric.RETRIEVAL_RECALL_AT_10
    )
    assert retrieval.status == NotebookMetricStatus.PROVISIONAL
    assert retrieval.eligible_denominator == 38
    assert retrieval.evaluated_denominator == 37
    assert retrieval.unavailable_count == 1


def test_notebook_foundation_probe_is_isolated_and_creates_no_baseline(tmp_path: Path) -> None:
    probe = probe_notebook_foundation_v1(tmp_path)
    assert probe.status == "FOUNDATION_READY_NON_QUALIFIED"
    assert probe.case_count == 40
    assert list(probe.slice_counts.values()) == [5, 6, 5, 6, 6, 5, 4, 3]
    assert probe.publication_count == 4
    assert probe.template_count == 2
    assert probe.revision_count == 3
    assert probe.execution_count == 4
    assert probe.secret_or_path_leakage == 0
    assert probe.database_sidecars == ()
    assert probe.baseline_run_created is False
    assert probe.baseline_metrics_created is False
    assert not (tmp_path / "evals").exists()


def test_reviewed_row_rejects_self_reported_truth_fields() -> None:
    payload = {
        "case_id": "nb-v1-001",
        "availability": "AVAILABLE",
        "candidates": [],
        "expected_entity_ids": ["fabricated"],
    }
    with pytest.raises(ValidationError, match="extra"):
        NotebookReviewedRow.model_validate(payload)
    with pytest.raises(ValidationError, match="must not contain"):
        NotebookReviewedRow(
            case_id="nb-v1-001",
            availability=NotebookReviewAvailability.ERROR,
            candidates=(
                NotebookReviewedCandidate(
                    entity_id="fabricated",
                    locator="notebook://project-notebook-golden-v1/fabricated",
                ),
            ),
        )
