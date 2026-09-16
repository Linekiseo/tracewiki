from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_rag.rag.sources.workspace.contracts import (
    WorkspaceEntityType,
    build_workspace_publication,
    build_workspace_retrieval_unit,
    build_workspace_tombstone,
    canonical_sha256,
    parse_workspace_locator,
)
from evidence_rag.rag.sources.workspace.evaluation_v1 import (
    EXPECTED_WORKSPACE_SLICE_COUNTS,
    WORKSPACE_GOLDEN_AUTHORITY_SHA256,
    WORKSPACE_GOLDEN_PACKAGE_SHA256,
    WorkspaceGoldenError,
    WorkspaceMetric,
    WorkspaceMetricStatus,
    WorkspaceReviewAvailability,
    WorkspaceReviewedCandidate,
    WorkspaceReviewedRow,
    build_workspace_golden_v1,
    evaluate_reviewed_workspace_retrieval,
)
from evidence_rag.rag.sources.workspace.fixture_v1 import (
    build_workspace_fixture_v1,
)
from evidence_rag.rag.sources.workspace.schema import WORKSPACE_ENTITY_TABLES
from evidence_rag.rag.sources.workspace.store import (
    WorkspacePublicationError,
    WorkspaceStorePathError,
    WorkspaceV2Store,
)


def test_workspace_golden_is_exact_content_addressed_and_described() -> None:
    dataset = build_workspace_golden_v1()
    rebuilt = build_workspace_golden_v1()
    fixture = build_workspace_fixture_v1()
    descriptor = json.loads(
        (Path(__file__).parent / "fixtures" / "workspace_golden_v1" / "release.json").read_text(
            encoding="utf-8"
        )
    )
    assert dataset == rebuilt
    assert dataset.package_sha256 == WORKSPACE_GOLDEN_PACKAGE_SHA256
    assert dataset.authority_sha256 == WORKSPACE_GOLDEN_AUTHORITY_SHA256
    assert len(dataset.cases) == 40
    assert tuple(item.case_id for item in dataset.cases) == tuple(
        f"workspace-v1-{index:03d}" for index in range(1, 41)
    )
    assert Counter(item.slice for item in dataset.cases) == Counter(EXPECTED_WORKSPACE_SLICE_COUNTS)
    assert len(fixture.publication.entities) == 111
    assert len(fixture.publication.edges) == 141
    assert len(fixture.publication.retrieval_units) == 111
    assert descriptor == {
        "authority_sha256": dataset.authority_sha256,
        "case_count": 40,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.dataset_version,
        "entity_counts": dict(
            sorted(Counter(item.entity_type.value for item in fixture.publication.entities).items())
        ),
        "fixture_recipe_sha256": fixture.recipe_sha256,
        "package_sha256": dataset.package_sha256,
        "publication_sha256": fixture.publication.publication_sha256,
        "slice_counts": dict(sorted(Counter(item.slice.value for item in dataset.cases).items())),
    }


def test_workspace_locator_round_trip_and_digest_are_fail_closed() -> None:
    fixture = build_workspace_fixture_v1()
    entity = fixture.publication.entities[0]
    assert parse_workspace_locator(entity.locator) == (
        entity.scope.project_id,
        entity.entity_type,
        entity.stable_id,
        entity.version,
    )
    with pytest.raises(ValueError, match="canonical"):
        parse_workspace_locator(entity.locator.replace("project-", "%70roject-", 1))
    with pytest.raises(ValidationError, match="digest"):
        entity.model_copy(update={"label": "tampered"})
    source_unit = fixture.publication.retrieval_units[0]
    bad_unit = build_workspace_retrieval_unit(
        **source_unit.model_dump(
            mode="python",
            exclude={"content_sha256", "entity_id"},
        ),
        entity_id="not-in-publication",
    )
    with pytest.raises(ValidationError, match="outside publication"):
        build_workspace_publication(
            publication_id="workspacepub-" + ("e" * 32),
            scope=fixture.publication.scope,
            entities=fixture.publication.entities,
            edges=fixture.publication.edges,
            retrieval_units=(bad_unit,),
            builder_version=fixture.publication.builder_version,
            published_at=fixture.publication.published_at,
        )


def test_workspace_isolated_store_is_typed_idempotent_and_tombstoned(
    tmp_path: Path,
) -> None:
    fixture = build_workspace_fixture_v1()
    store = WorkspaceV2Store(tmp_path / "workspace.sqlite", isolated_root=tmp_path)
    store.publish(fixture.publication)
    store.publish(fixture.publication)
    assert store.table_count("workspace_publications_v2") == 1
    assert store.table_count("workspace_entities_v2") == 111
    assert store.table_count("workspace_edges_v2") == 141
    assert store.table_count("workspace_retrieval_units_v2") == 111
    entity_counts = Counter(item.entity_type for item in fixture.publication.entities)
    for entity_type, table in WORKSPACE_ENTITY_TABLES.items():
        assert store.table_count(table) == entity_counts[entity_type]
    assert not Path(str(store.path) + "-wal").exists()
    assert not Path(str(store.path) + "-shm").exists()

    target = next(
        item
        for item in fixture.publication.entities
        if item.entity_type == WorkspaceEntityType.RISK
    )
    tombstone = build_workspace_tombstone(
        tombstone_id="tombstone-hazard-01",
        scope=target.scope,
        target_entity_id=target.entity_id,
        reason="source-deleted",
        effective_at="2026-07-29T09:00:00Z",
    )
    store.tombstone(tombstone)
    visible = store.list_entities(
        fixture.publication.scope,
        include_history=True,
        allowed_acl_refs=(fixture.publication.scope.acl_ref,),
        enforce_acl=True,
    )
    assert target.entity_id not in {item.entity_id for item in visible}
    with pytest.raises(WorkspacePublicationError, match="revive"):
        alternate = build_workspace_publication(
            publication_id="workspacepub-" + ("f" * 32),
            scope=fixture.publication.scope,
            entities=fixture.publication.entities,
            edges=fixture.publication.edges,
            retrieval_units=fixture.publication.retrieval_units,
            builder_version=fixture.publication.builder_version,
            published_at="2026-07-29T09:00:00Z",
        )
        store.publish(alternate)


def test_workspace_store_rejects_formal_escape_sidecar_and_symlink(
    tmp_path: Path,
) -> None:
    with pytest.raises(WorkspaceStorePathError, match="formal"):
        WorkspaceV2Store(tmp_path / "evidence-rag.sqlite3", isolated_root=tmp_path)
    with pytest.raises(WorkspaceStorePathError):
        WorkspaceV2Store(tmp_path.parent / "workspace.sqlite", isolated_root=tmp_path)
    (tmp_path / "workspace.sqlite-wal").write_bytes(b"forbidden")
    with pytest.raises(WorkspaceStorePathError, match="sidecars"):
        WorkspaceV2Store(tmp_path / "workspace.sqlite", isolated_root=tmp_path)
    (tmp_path / "workspace.sqlite-wal").unlink()
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(WorkspaceStorePathError, match="symlink"):
        WorkspaceV2Store(alias / "workspace.sqlite", isolated_root=tmp_path)


def test_workspace_evaluator_freezes_membership_truth_and_denominators() -> None:
    dataset = build_workspace_golden_v1()
    entities = dataset.entity_map()
    rows = tuple(
        WorkspaceReviewedRow(
            case_id=case.case_id,
            availability=WorkspaceReviewAvailability.AVAILABLE,
            candidates=tuple(
                WorkspaceReviewedCandidate(
                    entity_id=entity_id,
                    locator=entities[entity_id].locator,
                )
                for entity_id in case.expected_entity_ids
            ),
        )
        for case in dataset.cases
    )
    report = evaluate_reviewed_workspace_retrieval(rows, dataset=dataset)
    metric_map = {item.metric: item for item in report.metrics}
    assert all(item.status == WorkspaceMetricStatus.AVAILABLE for item in report.metrics)
    assert metric_map[WorkspaceMetric.SCOPE_RESOLUTION_ACCURACY].eligible_denominator == 40
    assert metric_map[WorkspaceMetric.ACCEPTANCE_GATE_ACCURACY].eligible_denominator == 6
    assert metric_map[WorkspaceMetric.EVIDENCE_COVERAGE_PRECISION].eligible_denominator == 6
    assert metric_map[WorkspaceMetric.UNSAFE_MUTATION_RATE].numerator == 0
    assert metric_map[WorkspaceMetric.ACL_LEAKAGE_RATE].numerator == 0
    assert all(item.numerator == item.eligible_denominator for item in report.slices)

    with pytest.raises(WorkspaceGoldenError, match="membership"):
        evaluate_reviewed_workspace_retrieval(rows, dataset=dataset, case_membership=())
    with pytest.raises(WorkspaceGoldenError, match="reordered"):
        evaluate_reviewed_workspace_retrieval(
            (rows[1], rows[0], *rows[2:]),
            dataset=dataset,
        )
    with pytest.raises(ValidationError, match="extra"):
        WorkspaceReviewedRow.model_validate(
            {
                "case_id": rows[0].case_id,
                "availability": "AVAILABLE",
                "candidates": [],
                "expected_entity_ids": ["fabricated"],
            }
        )
    bad_locator = rows[0].model_copy(
        update={
            "candidates": (
                rows[0]
                .candidates[0]
                .model_copy(update={"locator": "workspace://fabricated/project/x?version=1"}),
            )
        }
    )
    with pytest.raises(WorkspaceGoldenError, match="authority"):
        evaluate_reviewed_workspace_retrieval(
            (bad_locator, *rows[1:]),
            dataset=dataset,
        )


def test_workspace_fixture_has_no_embedded_secret_or_absolute_path() -> None:
    fixture = build_workspace_fixture_v1()
    rendered = fixture.model_dump_json()
    assert "/Users/" not in rendered
    assert "\\\\" not in rendered
    assert "api_key=" not in rendered.casefold()
    assert canonical_sha256(fixture.publication.model_dump(mode="json")).startswith("sha256:")
