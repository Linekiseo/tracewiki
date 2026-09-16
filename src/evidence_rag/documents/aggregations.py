from __future__ import annotations

import math
import statistics
from typing import Any
from uuid import uuid4

from ..workspace.models import RelationCreate
from ..workspace.service import WorkspaceService
from .models import ClaimEvidenceCreate, TableMetricAggregationCreate
from .store import DocumentStore
from .table_evidence import ClaimEvidenceManager, normalized_metric_text, numeric_value


class TableAggregationError(ValueError):
    pass


class TableAggregationService:
    """Records reproducible multi-Run reductions behind reported table values."""

    def __init__(self, store: DocumentStore, workspace: WorkspaceService) -> None:
        self.store = store
        self.workspace = workspace

    def create(
        self, request: TableMetricAggregationCreate, claim_evidence: ClaimEvidenceManager
    ) -> dict[str, Any]:
        self.workspace._require_project(request.project_id)
        cell = self.store.get_table_cell(request.table_cell_id)
        if not cell or cell["project_id"] != request.project_id:
            raise TableAggregationError("table cell not found in project")
        claim = self.store.get_claim(request.claim_id) if request.claim_id else None
        if request.claim_id and (
            not claim
            or claim["project_id"] != request.project_id
            or claim["document_id"] != cell["document_id"]
        ):
            raise TableAggregationError("claim does not belong to the table cell document")

        requested_ids = list(dict.fromkeys(request.metric_ids))
        excluded = set(request.excluded_metric_ids)
        if not excluded.issubset(requested_ids):
            raise TableAggregationError("excluded metrics must be included in metric_ids")
        metrics_by_id = {
            item["id"]: item for item in self.store.list_project_metrics(request.project_id)
        }
        missing = [metric_id for metric_id in requested_ids if metric_id not in metrics_by_id]
        if missing:
            raise TableAggregationError(f"metrics not found in project: {', '.join(missing[:5])}")
        metrics = [
            metrics_by_id[metric_id] for metric_id in requested_ids if metric_id not in excluded
        ]
        if len(metrics) < 2:
            raise TableAggregationError("aggregation requires at least two included metrics")
        names = {normalized_metric_text(item["name"]) for item in metrics}
        units = {item.get("unit") or "" for item in metrics}
        splits = {item.get("split") or "" for item in metrics}
        if len(names) != 1 or len(units) != 1 or len(splits) != 1:
            raise TableAggregationError(
                "aggregated metrics must share metric name, unit and evaluation split"
            )

        values = [float(item["value"]) for item in metrics]
        computed = self._aggregate(values, request.aggregation_function)
        reported = numeric_value(cell["value"])
        if reported is None:
            raise TableAggregationError("table cell does not contain a numeric value")
        existing = next(
            (
                item
                for item in self.store.list_table_metric_aggregations(
                    request.project_id,
                    document_id=cell["document_id"],
                    claim_id=request.claim_id,
                )
                if item["table_cell_id"] == cell["id"]
                and item.get("claim_id") == request.claim_id
                and item["aggregation_function"] == request.aggregation_function
                and set(item["metric_ids"]) == {metric["id"] for metric in metrics}
                and set(item["excluded_metric_ids"]) == excluded
                and math.isclose(item["tolerance"], request.tolerance)
            ),
            None,
        )
        if existing:
            return {"aggregation": existing, "claim_evidence": None, "created": False}
        verified = self._matches(reported, computed, cell["value"], request.tolerance)
        token = uuid4().hex
        record = self.store.create_table_metric_aggregation(
            {
                "id": f"metric-aggregation://{request.project_id}/{token}",
                "display_key": f"AGG-{token[:8].upper()}",
                "project_id": request.project_id,
                "document_id": cell["document_id"],
                "claim_id": request.claim_id,
                "table_cell_id": cell["id"],
                "metric_name": metrics[0]["name"],
                "aggregation_function": request.aggregation_function,
                "metric_ids": [item["id"] for item in metrics],
                "run_ids": list(dict.fromkeys(item["run_id"] for item in metrics)),
                "excluded_metric_ids": list(excluded),
                "sample_count": len(values),
                "computed_value": computed,
                "reported_value": reported,
                "variance": statistics.pvariance(values),
                "tolerance": request.tolerance,
                "status": "verified" if verified else "contradicted",
                "actor": request.actor,
                "note": request.note,
            }
        )
        self._create_relations(record, metrics)
        evidence = None
        if claim:
            evidence = claim_evidence.add_evidence(
                ClaimEvidenceCreate(
                    claim_id=claim["id"],
                    evidence_entity_id=record["id"],
                    evidence_type="metric_aggregation",
                    relationship="supports" if verified else "refutes",
                    confidence=1.0,
                    note=request.note
                    or "Recorded multi-Run aggregation for the reported table value.",
                    actor=request.actor,
                )
            )
        self.workspace._audit(
            request.project_id,
            "table_metric.aggregation_created",
            "table_metric_aggregation",
            record["id"],
            {
                "metric_ids": record["metric_ids"],
                "function": record["aggregation_function"],
                "status": record["status"],
            },
            actor=request.actor,
        )
        return {"aggregation": record, "claim_evidence": evidence, "created": True}

    def _create_relations(self, aggregation: dict[str, Any], metrics: list[dict[str, Any]]) -> None:
        self.workspace.create_relation(
            RelationCreate(
                project_id=aggregation["project_id"],
                source_entity_id=aggregation["table_cell_id"],
                predicate="derived_from",
                target_entity_id=aggregation["id"],
                evidence_entity_id=aggregation["id"],
                derivation="human_confirmed",
                confidence=1.0,
                review_status="confirmed",
                rule_version="table-metric-aggregation-v1",
                metadata={
                    "function": aggregation["aggregation_function"],
                    "status": aggregation["status"],
                },
            )
        )
        for metric in metrics:
            self.workspace.create_relation(
                RelationCreate(
                    project_id=aggregation["project_id"],
                    source_entity_id=aggregation["id"],
                    predicate="aggregates",
                    target_entity_id=metric["id"],
                    evidence_entity_id=aggregation["id"],
                    derivation="human_confirmed",
                    confidence=1.0,
                    review_status="confirmed",
                    rule_version="table-metric-aggregation-v1",
                    metadata={"run_id": metric["run_id"], "value": metric["value"]},
                )
            )

    @staticmethod
    def _aggregate(values: list[float], function: str) -> float:
        operations = {
            "mean": statistics.mean,
            "median": statistics.median,
            "sum": sum,
            "min": min,
            "max": max,
        }
        return float(operations[function](values))

    @staticmethod
    def _matches(reported: float, computed: float, raw_value: str, tolerance: float) -> bool:
        candidates = [computed * 100] if "%" in raw_value else [computed]
        return any(
            math.isclose(reported, value, rel_tol=tolerance, abs_tol=tolerance)
            for value in candidates
        )
