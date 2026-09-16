from __future__ import annotations

from pathlib import Path

import pytest

from evidence_rag.rag.sources.experiment.contracts_v2 import (
    ExperimentMetricRegistryEntryV2,
    MetricDirectionV2,
    build_experiment_run_group_v2,
    build_experiment_run_snapshot_v2,
    canonical_sha256_v2,
)
from evidence_rag.rag.sources.experiment.store_v2 import (
    ExperimentStoreV2,
    ExperimentStoreV2Error,
)


def _snapshot(generation_id: str, run_id: str = "run-1", value: float = 0.9):
    return build_experiment_run_snapshot_v2(
        {
            "id": run_id,
            "experiment_id": "experiment-1",
            "status": "completed",
            "dataset_id": "dataset-1",
            "dataset_version": "v1",
            "config": {"seed": 7},
            "environment": {"python": "3.12"},
            "metrics": [{"name": "accuracy", "value": value, "unit": "ratio"}],
        },
        project_id="project-1",
        source_id="source-1",
        generation_id=generation_id,
        acl_ref="acl-1",
        registry={
            "accuracy": ExperimentMetricRegistryEntryV2(
                canonical_name="accuracy",
                aliases=("acc",),
                description="Classification accuracy.",
                unit="ratio",
                direction=MetricDirectionV2.HIGHER,
                provenance="test-registry-v1",
            )
        },
        observed_at="2026-07-29T00:00:00Z",
    )


def _store(tmp_path: Path) -> ExperimentStoreV2:
    root = tmp_path / "isolated"
    root.mkdir()
    store = ExperimentStoreV2(root / "experiment-v2.sqlite3", isolated_root=root)
    store.initialize()
    return store


def test_additive_publish_is_atomic_scoped_and_idempotent(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        generation = store.prepare_generation(
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
            watermark="watermark-1",
            expected_snapshots=1,
        )
        snapshot = _snapshot(generation.generation_id)
        group = build_experiment_run_group_v2("seeds", (snapshot,))
        payload = {"title": "accuracy surface", "snapshot": snapshot.run_snapshot_id}
        unit = {
            "unit_id": "experimentunit-" + canonical_sha256_v2(payload)[7:],
            "run_snapshot_id": snapshot.run_snapshot_id,
            "unit_type": "metric.definition",
            "content_sha256": canonical_sha256_v2(payload),
            "payload": payload,
        }
        receipt = store.publish(generation, (snapshot,), groups=(group,), retrieval_units=(unit,))
        assert receipt.snapshots == 1
        assert receipt.observations == 1
        assert receipt.groups == 1
        assert store.publish(
            generation,
            (snapshot,),
            groups=(group,),
            retrieval_units=(unit,),
        ).idempotent
        assert store.active_snapshots(
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
        ) == (snapshot,)
        assert (
            store.active_snapshots(
                project_id="project-1",
                source_id="source-1",
                acl_ref="other",
            )
            == ()
        )


def test_new_generation_and_rollback_preserve_immutable_history(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        first = store.prepare_generation(
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
            watermark="watermark-1",
            expected_snapshots=1,
        )
        first_snapshot = _snapshot(first.generation_id)
        store.publish(first, (first_snapshot,))
        second = store.prepare_generation(
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
            watermark="watermark-2",
            expected_snapshots=1,
        )
        second_snapshot = _snapshot(second.generation_id, value=0.8)
        store.publish(second, (second_snapshot,))
        assert store.active_snapshots(
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
        ) == (second_snapshot,)
        store.rollback_to(
            project_id="project-1",
            source_id="source-1",
            generation_id=first.generation_id,
        )
        assert store.active_snapshots(
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
        ) == (first_snapshot,)
        assert store.counts()["run_snapshots"] == 2


def test_failed_publish_exposes_no_partial_generation(tmp_path: Path) -> None:
    with _store(tmp_path) as store:
        generation = store.prepare_generation(
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
            watermark="watermark-1",
            expected_snapshots=2,
        )
        with pytest.raises(ExperimentStoreV2Error, match="count"):
            store.publish(generation, (_snapshot(generation.generation_id),))
        assert (
            store.active_snapshots(
                project_id="project-1",
                source_id="source-1",
                acl_ref="acl-1",
            )
            == ()
        )


def test_store_rejects_existing_formal_and_escape_paths(tmp_path: Path) -> None:
    root = tmp_path / "isolated"
    root.mkdir()
    with pytest.raises(ExperimentStoreV2Error):
        ExperimentStoreV2(root / "evidence-rag.sqlite3", isolated_root=root)
    with pytest.raises(ExperimentStoreV2Error):
        ExperimentStoreV2(tmp_path / "outside.sqlite3", isolated_root=root)
    existing = root / "existing.sqlite3"
    existing.touch()
    with pytest.raises(ExperimentStoreV2Error):
        ExperimentStoreV2(existing, isolated_root=root)
