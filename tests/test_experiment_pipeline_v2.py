from __future__ import annotations

from pathlib import Path

from evidence_rag.rag.sources.experiment.contracts_v2 import (
    ExperimentMetricRegistryEntryV2,
    MetricDirectionV2,
)
from evidence_rag.rag.sources.experiment.pipeline_v2 import (
    ExperimentBuildInputV2,
    ExperimentSourcePipelineV2,
)
from evidence_rag.rag.sources.experiment.query_v2 import parse_experiment_query_v2
from evidence_rag.rag.sources.experiment.store_v2 import ExperimentStoreV2


def test_full_isolated_pipeline_publishes_and_queries(tmp_path: Path) -> None:
    root = tmp_path / "isolated"
    root.mkdir()
    with ExperimentStoreV2(root / "v2.sqlite3", isolated_root=root) as store:
        store.initialize()
        pipeline = ExperimentSourcePipelineV2(store)
        prepared = pipeline.prepare(
            ExperimentBuildInputV2(
                project_id="project-1",
                source_id="source-1",
                acl_ref="acl-1",
                watermark="watermark-1",
                observed_at="2026-07-29T00:00:00Z",
                runs=(
                    {
                        "id": "run-1",
                        "experiment_id": "experiment-1",
                        "status": "completed",
                        "dataset_id": "dataset-1",
                        "dataset_version": "v1",
                        "config": {"seed": 1},
                        "environment": {"python": "3.12"},
                        "metrics": [{"name": "accuracy", "value": 0.9, "unit": "ratio"}],
                    },
                ),
                experiment_metadata={
                    "experiment-1": {
                        "title": "Vision benchmark",
                        "objective": "Improve classification",
                    }
                },
                registry={
                    "accuracy": ExperimentMetricRegistryEntryV2(
                        canonical_name="accuracy",
                        aliases=("acc",),
                        description="Classification accuracy.",
                        unit="ratio",
                        direction=MetricDirectionV2.HIGHER,
                        provenance="registry-v1",
                    )
                },
            )
        )
        receipt = pipeline.publish(prepared)
        assert receipt.snapshots == 1
        query = parse_experiment_query_v2(
            {
                "task": "best",
                "run_ids": ["run-1"],
                "statuses": ["completed"],
            },
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-1",
        )
        result = pipeline.query(query, semantic_query="vision accuracy")
        assert result.structured_snapshot_ids == (prepared.snapshots[0].run_snapshot_id,)
        assert result.semantic.pinned_count > 0
        assert result.context.blocks
        assert result.context.acl_ref == "acl-1"


def test_pipeline_acl_scope_is_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "isolated"
    root.mkdir()
    with ExperimentStoreV2(root / "v2.sqlite3", isolated_root=root) as store:
        store.initialize()
        pipeline = ExperimentSourcePipelineV2(store)
        wrong_scope = parse_experiment_query_v2(
            {"task": "search"},
            project_id="project-1",
            source_id="source-1",
            acl_ref="acl-other",
        )
        try:
            pipeline.query(wrong_scope, semantic_query="anything")
        except ValueError as exc:
            assert "no governed" in str(exc)
        else:
            raise AssertionError("empty or wrong ACL scope must fail closed")
