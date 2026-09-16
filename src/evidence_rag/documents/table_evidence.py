from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Protocol
from uuid import uuid4

from ..workspace.models import RelationCreate, RelationReview
from ..workspace.service import WorkspaceService
from .models import ClaimEvidenceCreate, ClaimEvidenceUnlink, TableMetricMatchReview
from .store import DocumentStore


class ClaimEvidenceManager(Protocol):
    def add_evidence(self, request: ClaimEvidenceCreate) -> dict[str, Any]: ...

    def unlink_evidence(self, request: ClaimEvidenceUnlink) -> dict[str, Any]: ...


class TableEvidenceError(ValueError):
    pass


class TableEvidenceService:
    """Owns explainable TableCell → MetricResult candidate and review workflows."""

    def __init__(self, store: DocumentStore, workspace: WorkspaceService) -> None:
        self.store = store
        self.workspace = workspace

    def scan(self, project_id: str, document_id: str | None = None) -> dict[str, Any]:
        self.workspace._require_project(project_id)
        tables = self.store.list_table_contexts(project_id, document_id)
        metrics = self.store.list_project_metrics(project_id)
        claims = self.store.list_claims(project_id, document_id=document_id)
        claims_by_document: dict[str, list[dict[str, Any]]] = {}
        for claim in claims:
            claims_by_document.setdefault(claim["document_id"], []).append(claim)
        candidates = []
        for table in tables:
            cells = table["cells"]
            by_position = {(cell["row_index"], cell["column_index"]): cell for cell in cells}
            for cell in (item for item in cells if not item["is_header"]):
                if numeric_value(cell["value"]) is None:
                    continue
                column_header = by_position.get((0, cell["column_index"]), {}).get("value", "")
                row_header = by_position.get((cell["row_index"], 0), {}).get("value", "")
                row_text = " ".join(
                    item["value"] for item in cells if item["row_index"] == cell["row_index"]
                )
                context = " ".join(
                    part
                    for part in (
                        table.get("title"),
                        table.get("caption"),
                        column_header,
                        row_header,
                        row_text,
                    )
                    if part
                )
                for metric in metrics:
                    score, signals = self._score(cell["value"], context, metric)
                    if score < 0.75:
                        continue
                    matching_claims = [
                        claim
                        for claim in claims_by_document.get(table["document_id"], [])
                        if self._claim_matches(claim, cell["value"], metric["name"])
                    ]
                    for claim in matching_claims or [None]:
                        claim_id = claim["id"] if claim else None
                        token = hashlib.sha256(
                            f"{claim_id or '_'}\x1f{cell['id']}\x1f{metric['id']}".encode()
                        ).hexdigest()
                        candidates.append(
                            self.store.upsert_table_metric_candidate(
                                {
                                    "id": f"table-metric-match://sha256:{token}",
                                    "project_id": project_id,
                                    "document_id": table["document_id"],
                                    "claim_id": claim_id,
                                    "table_id": table["id"],
                                    "table_cell_id": cell["id"],
                                    "metric_id": metric["id"],
                                    "run_id": metric["run_id"],
                                    "score": min(1.0, score + (0.1 if claim else 0.0)),
                                    "signals": {
                                        **signals,
                                        "column_header": column_header,
                                        "row_header": row_header,
                                        "cell_value": cell["value"],
                                        "claim_numeric_exact": bool(claim),
                                        "derivation": "rule_derived",
                                    },
                                }
                            )
                        )
        return {"tables": len(tables), "metrics": len(metrics), "candidates": candidates}

    def review(
        self,
        candidate_id: str,
        request: TableMetricMatchReview,
        claim_evidence: ClaimEvidenceManager,
    ) -> dict[str, Any]:
        candidate = self._require_candidate(candidate_id)
        evidence_result = None
        evidence_id = None
        if request.decision == "confirmed":
            self._ensure_relation(
                candidate["project_id"],
                candidate["run_id"],
                "reports",
                candidate["metric_id"],
                candidate["metric_id"],
                derivation="deterministic",
                confidence=1.0,
                rule_version="run-metric-record-v1",
                metadata={"source": "experiment_run"},
                reviewer=request.reviewer,
            )
            self._ensure_relation(
                candidate["project_id"],
                candidate["table_cell_id"],
                "reports",
                candidate["metric_id"],
                candidate["table_cell_id"],
                derivation="human_confirmed",
                confidence=candidate["score"],
                rule_version="table-metric-match-v1",
                metadata={
                    "table_metric_candidate_id": candidate["id"],
                    "signals": candidate["signals"],
                },
                reviewer=request.reviewer,
            )
            if candidate.get("claim_id"):
                evidence_result = claim_evidence.add_evidence(
                    ClaimEvidenceCreate(
                        claim_id=candidate["claim_id"],
                        evidence_entity_id=candidate["table_cell_id"],
                        evidence_type="table_cell",
                        relationship="supports",
                        confidence=candidate["score"],
                        note=request.note or "Confirmed TableCell to MetricResult evidence chain.",
                        actor=request.reviewer,
                    )
                )
                evidence_id = evidence_result["evidence"]["id"]
        else:
            evidence_result = self._reject(candidate, request, claim_evidence)
        reviewed = self.store.review_table_metric_candidate(
            {
                "id": f"table-metric-review://{uuid4().hex}",
                "project_id": candidate["project_id"],
                "candidate_id": candidate["id"],
                "decision": request.decision,
                "reviewer": request.reviewer,
                "note": request.note,
                "claim_evidence_id": evidence_id,
            }
        )
        self.workspace._audit(
            candidate["project_id"],
            "table_metric.match_reviewed",
            "table_metric_match_candidate",
            candidate["id"],
            request.model_dump(),
            actor=request.reviewer,
        )
        return {"candidate": reviewed, "claim_evidence": evidence_result}

    def _reject(
        self,
        candidate: dict[str, Any],
        request: TableMetricMatchReview,
        claim_evidence: ClaimEvidenceManager,
    ) -> dict[str, Any] | None:
        evidence_result = None
        previous = next(
            (
                item
                for item in candidate.get("reviews", [])
                if item["decision"] == "confirmed" and item.get("claim_evidence_id")
            ),
            None,
        )
        if previous and candidate.get("claim_id"):
            try:
                evidence_result = claim_evidence.unlink_evidence(
                    ClaimEvidenceUnlink(
                        claim_id=candidate["claim_id"],
                        evidence_id=previous["claim_evidence_id"],
                        actor=request.reviewer,
                        note=request.note or "Table-to-metric match rejected after review.",
                    )
                )
            except ValueError:
                evidence_result = None
        for edge in self.workspace.store.list_relations(
            candidate["project_id"], entity_id=candidate["table_cell_id"], limit=500
        ):
            if (
                edge["source_entity_id"] == candidate["table_cell_id"]
                and edge["target_entity_id"] == candidate["metric_id"]
                and edge["predicate"] == "reports"
                and edge.get("metadata", {}).get("table_metric_candidate_id") == candidate["id"]
                and edge["review_status"] != "rejected"
            ):
                self.workspace.review_relation(
                    edge["id"],
                    RelationReview(
                        review_status="rejected",
                        reviewer=request.reviewer,
                        note=request.note or "Table-to-metric match rejected.",
                    ),
                )
        return evidence_result

    def _ensure_relation(
        self,
        project_id: str,
        source_entity_id: str,
        predicate: str,
        target_entity_id: str,
        evidence_entity_id: str,
        *,
        derivation: str,
        confidence: float,
        rule_version: str,
        metadata: dict[str, Any],
        reviewer: str,
    ) -> dict[str, Any]:
        existing = next(
            (
                edge
                for edge in self.workspace.store.list_relations(
                    project_id, entity_id=source_entity_id, limit=500
                )
                if edge["source_entity_id"] == source_entity_id
                and edge["predicate"] == predicate
                and edge["target_entity_id"] == target_entity_id
                and edge.get("evidence_entity_id") == evidence_entity_id
            ),
            None,
        )
        if existing:
            if existing["review_status"] != "confirmed":
                return self.workspace.review_relation(
                    existing["id"],
                    RelationReview(
                        review_status="confirmed",
                        reviewer=reviewer,
                        note="Evidence chain reconfirmed.",
                    ),
                )
            return existing
        return self.workspace.create_relation(
            RelationCreate(
                project_id=project_id,
                source_entity_id=source_entity_id,
                predicate=predicate,
                target_entity_id=target_entity_id,
                evidence_entity_id=evidence_entity_id,
                derivation=derivation,
                confidence=confidence,
                review_status="confirmed",
                rule_version=rule_version,
                metadata=metadata,
            )
        )

    @staticmethod
    def _score(
        cell_value: str, context: str, metric: dict[str, Any]
    ) -> tuple[float, dict[str, Any]]:
        score = 0.0
        signals: dict[str, Any] = {}
        if numeric_value_matches(cell_value, float(metric["value"])):
            score += 0.55
            signals["numeric_exact"] = True
        normalized_context = normalized_metric_text(context)
        normalized_metric = normalized_metric_text(metric["name"])
        if normalized_metric and normalized_metric in normalized_context:
            score += 0.25
            signals["metric_label"] = metric["name"]
        run_ids = [metric.get("run_key"), metric.get("external_id"), metric.get("run_id")]
        explicit_ids = [
            str(value) for value in run_ids if value and str(value).casefold() in context.casefold()
        ]
        if explicit_ids:
            score += 0.2
            signals["explicit_run_ids"] = explicit_ids
        if metric.get("unit") and str(metric["unit"]).casefold() in context.casefold():
            score += 0.05
            signals["unit"] = metric["unit"]
        return min(1.0, score), signals

    @staticmethod
    def _claim_matches(claim: dict[str, Any], cell_value: str, metric_name: str) -> bool:
        values = claim.get("metadata", {}).get("reported_values", [])
        if not any(numeric_value_matches(cell_value, float(value)) for value in values):
            return False
        return normalized_metric_text(metric_name) in normalized_metric_text(claim["content"])

    def _require_candidate(self, candidate_id: str) -> dict[str, Any]:
        item = self.store.get_table_metric_candidate(candidate_id)
        if not item:
            raise TableEvidenceError("table metric match candidate not found")
        return item


def normalized_metric_text(value: str) -> str:
    return re.sub(r"[^a-z0-9@]+", "", str(value).casefold())


def numeric_value(value: str) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def numeric_value_matches(raw_value: str, observed: float) -> bool:
    value = numeric_value(raw_value)
    if value is None:
        return False
    variants = [observed]
    if "%" in str(raw_value):
        variants.append(observed * 100)
    else:
        variants.extend([observed * 100, observed / 100])
    return any(math.isclose(value, candidate, rel_tol=1e-4, abs_tol=1e-9) for candidate in variants)
