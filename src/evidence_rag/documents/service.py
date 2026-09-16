from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from ..config import Settings
from ..experiments.store import ExperimentStore
from ..sources.models import SourceEventInput
from ..sources.service import RawSourceService
from ..storage import utc_now
from ..workspace.models import IterationLinkCreate, RelationCreate, RelationReview
from ..workspace.service import WorkspaceService
from .adapters import ParsedDocument, StructuredDocumentAdapter
from .adapters.structured import PDFParserProvider
from .aggregations import TableAggregationService
from .models import (
    ClaimCreate,
    ClaimEvidenceCreate,
    ClaimEvidenceUnlink,
    ClaimMatchReview,
    ClaimUpdate,
    DocumentIngestRequest,
)
from .store import DocumentStore
from .table_evidence import TableEvidenceService, numeric_value_matches


class DocumentError(ValueError):
    pass


class DocumentService:
    def __init__(
        self,
        store: DocumentStore,
        experiments: ExperimentStore,
        workspace: WorkspaceService,
        settings: Settings,
        sources: RawSourceService | None = None,
        pdf_provider: PDFParserProvider | None = None,
    ) -> None:
        self.store = store
        self.experiments = experiments
        self.workspace = workspace
        self.settings = settings
        self.sources = sources
        self.adapter = StructuredDocumentAdapter(pdf_provider=pdf_provider)
        self.table_evidence = TableEvidenceService(store, workspace)
        self.table_aggregations = TableAggregationService(store, workspace)

    def ingest(self, request: DocumentIngestRequest) -> dict[str, Any]:
        self.workspace._require_project(request.project_id)
        if request.iteration_id:
            iteration = self.workspace._require_iteration(request.iteration_id)
            if iteration["project_id"] != request.project_id:
                raise DocumentError("iteration does not belong to project")
        existing = self.store.find_version(request.project_id, request.title, request.version)
        if existing:
            return existing
        parsed, raw_payload, media_type = self._content(request)
        content = parsed.content
        token = hashlib.sha256(f"{request.project_id}\x1f{request.title}".encode()).hexdigest()[:24]
        document_id = (
            f"document://{request.project_id}/{token}/{quote(request.version, safe='._-')}"
        )
        sections = []
        for item in parsed.sections:
            ordinal = int(item["ordinal"])
            sections.append(
                {
                    **item,
                    "id": f"section://{token}/{quote(request.version, safe='._-')}/{ordinal}",
                }
            )
        pages = [
            {
                **item,
                "id": f"page://{token}/{quote(request.version, safe='._-')}/{item['page_number']}",
            }
            for item in parsed.pages
        ]
        tables = [
            {
                **item,
                "id": f"table://{token}/{quote(request.version, safe='._-')}/{item['ordinal']}",
            }
            for item in parsed.tables
        ]
        figures = [
            {
                **item,
                "id": f"figure://{token}/{quote(request.version, safe='._-')}/{item['ordinal']}",
            }
            for item in parsed.figures
        ]
        citations = [
            {
                **item,
                "id": f"citation://{token}/{quote(request.version, safe='._-')}/{item['ordinal']}",
            }
            for item in parsed.citations
        ]
        claims = self._claims(document_id, sections) if request.extract_claims else []
        document = {
            "id": document_id,
            "display_key": f"DOC-{token[:8].upper()}",
            "project_id": request.project_id,
            "iteration_id": request.iteration_id,
            "title": request.title,
            "version": request.version,
            "source_type": parsed.source_type,
            "source_uri": parsed.source_uri,
            "content_hash": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
            "authors": request.authors,
            "tags": request.tags,
        }
        raw = None
        if self.sources:
            raw = self.sources.accept(
                SourceEventInput(
                    source_type="document",
                    source_instance=parsed.source_uri,
                    event_type="document.version.observed",
                    source_object_id=f"{request.project_id}/{request.title}",
                    source_version=request.version,
                    project_id=request.project_id,
                    acl_ref=f"project:{request.project_id}",
                    source_uri=parsed.source_uri,
                    payload=raw_payload,
                    media_type=media_type,
                    adapter_version=self.adapter.adapter_version,
                    schema_version="structured-document-v2",
                    metadata={
                        "content_hash": document["content_hash"],
                        "parse_provenance": parsed.metadata,
                    },
                )
            )["raw_object"]
            if raw["state"] == "quarantined":
                raise DocumentError(
                    "document quarantined because high-confidence secrets were found"
                )
        result = self.store.create_document(
            document,
            sections,
            claims,
            pages=pages,
            tables=tables,
            figures=figures,
            citations=citations,
        )
        if self.sources and raw:
            self.sources.store.link_derivations(
                raw["id"],
                [
                    document_id,
                    *[item["id"] for item in sections],
                    *[item["id"] for item in pages],
                    *[item["id"] for item in tables],
                    *[item["id"] for item in figures],
                    *[item["id"] for item in citations],
                    *[item["id"] for item in claims],
                ],
                kind="document_parse",
                generation_id=None,
                derivation_version=self.adapter.adapter_version,
            )
        if request.iteration_id:
            self.workspace.link_iteration(
                IterationLinkCreate(
                    iteration_id=request.iteration_id,
                    entity_id=result["id"],
                    entity_type="ScientificDocument",
                    source_type="document",
                    role="research_output",
                )
            )
        self.suggest_matches(request.project_id, [item["id"] for item in claims])
        self.table_evidence.scan(request.project_id, document_id=document_id)
        return self.store.get_document(result["id"]) or result

    def create_claim(self, request: ClaimCreate) -> dict[str, Any]:
        document = self._require_document(request.document_id)
        token = uuid4().hex
        section = next(
            (item for item in document["sections"] if item["id"] == request.section_id), None
        )
        if request.section_id and not section:
            raise DocumentError("section does not belong to document")
        return self.store.create_claim(
            {
                **request.model_dump(),
                "id": f"claim://{document['project_id']}/{token}",
                "display_key": f"CLM-{token[:8].upper()}",
                "project_id": document["project_id"],
                "source_locator": section["source_locator"] if section else document["source_uri"],
            }
        )

    def update_claim(self, claim_id: str, request: ClaimUpdate) -> dict[str, Any]:
        self._require_claim(claim_id)
        return self.store.update_claim(claim_id, request.model_dump(exclude_none=True)) or {}

    def add_evidence(self, request: ClaimEvidenceCreate) -> dict[str, Any]:
        claim = self._require_claim(request.claim_id)
        if not self.workspace.store.entity_exists(request.evidence_entity_id):
            raise DocumentError("claim evidence entity does not exist")
        existing = next(
            (
                item
                for item in claim["evidence"]
                if item["evidence_entity_id"] == request.evidence_entity_id
                and item["relationship"] == request.relationship
            ),
            None,
        )
        token = uuid4().hex
        link = self.store.add_evidence(
            {
                **request.model_dump(),
                "id": f"claim-evidence://{token}",
                "project_id": claim["project_id"],
            }
        )
        predicate = {
            "supports": "supported_by",
            "refutes": "refuted_by",
            "qualifies": "qualified_by",
        }[request.relationship]
        if not existing:
            matching_edge = next(
                (
                    edge
                    for edge in self.workspace.store.list_relations(
                        claim["project_id"], entity_id=claim["id"], limit=500
                    )
                    if edge["source_entity_id"] == claim["id"]
                    and edge["target_entity_id"] == request.evidence_entity_id
                    and edge["predicate"] == predicate
                ),
                None,
            )
            if matching_edge:
                self.workspace.review_relation(
                    matching_edge["id"],
                    RelationReview(
                        review_status="confirmed",
                        reviewer=request.actor,
                        note=request.note or "Claim evidence relinked",
                    ),
                )
            else:
                self.workspace.create_relation(
                    RelationCreate(
                        project_id=claim["project_id"],
                        source_entity_id=claim["id"],
                        predicate=predicate,
                        target_entity_id=request.evidence_entity_id,
                        evidence_entity_id=request.evidence_entity_id,
                        derivation="human_confirmed",
                        confidence=request.confidence,
                        review_status="confirmed",
                        rule_version="claim-evidence-v1",
                        metadata={
                            "claim_evidence_id": link["id"],
                            "note": request.note,
                            "actor": request.actor,
                        },
                    )
                )
            self.store.record_evidence_event(
                {
                    "id": f"claim-evidence-event://{uuid4().hex}",
                    "project_id": claim["project_id"],
                    "claim_id": claim["id"],
                    "claim_evidence_id": link["id"],
                    "action": "linked",
                    "actor": request.actor,
                    "note": request.note,
                    "snapshot": link,
                }
            )
        return {"evidence": link, "validation": self.validate(claim["id"])}

    def unlink_evidence(self, request: ClaimEvidenceUnlink) -> dict[str, Any]:
        claim = self._require_claim(request.claim_id)
        removed = self.store.remove_evidence(request.claim_id, request.evidence_id)
        if not removed:
            raise DocumentError("claim evidence not found")
        predicate = {
            "supports": "supported_by",
            "refutes": "refuted_by",
            "qualifies": "qualified_by",
        }[removed["relationship"]]
        for edge in self.workspace.store.list_relations(
            claim["project_id"], entity_id=claim["id"], limit=500
        ):
            if (
                edge["source_entity_id"] == claim["id"]
                and edge["target_entity_id"] == removed["evidence_entity_id"]
                and edge["predicate"] == predicate
                and edge["review_status"] != "rejected"
            ):
                self.workspace.review_relation(
                    edge["id"],
                    RelationReview(
                        review_status="rejected",
                        reviewer=request.actor,
                        note=request.note or "Claim evidence unlinked",
                    ),
                )
        self.store.record_evidence_event(
            {
                "id": f"claim-evidence-event://{uuid4().hex}",
                "project_id": claim["project_id"],
                "claim_id": claim["id"],
                "claim_evidence_id": removed["id"],
                "action": "unlinked",
                "actor": request.actor,
                "note": request.note,
                "snapshot": removed,
            }
        )
        self.workspace._audit(
            claim["project_id"],
            "claim.evidence_unlinked",
            "claim_evidence",
            removed["id"],
            {"claim_id": claim["id"], "evidence_entity_id": removed["evidence_entity_id"]},
            actor=request.actor,
        )
        return {"removed": removed, "validation": self.validate(claim["id"])}

    def review_match_candidate(
        self, candidate_id: str, request: ClaimMatchReview
    ) -> dict[str, Any]:
        candidate = self._require_candidate(candidate_id)
        evidence_result = None
        evidence_id = None
        if request.decision == "confirmed":
            evidence_result = self.add_evidence(
                ClaimEvidenceCreate(
                    claim_id=candidate["claim_id"],
                    evidence_entity_id=candidate["run_id"],
                    evidence_type="experiment_run",
                    relationship=request.relationship,
                    confidence=candidate["score"],
                    note=request.note or "Confirmed from document-to-run match candidate.",
                    actor=request.reviewer,
                )
            )
            evidence_id = evidence_result["evidence"]["id"]
        elif candidate.get("reviews"):
            previous = next(
                (
                    item
                    for item in candidate["reviews"]
                    if item["decision"] == "confirmed" and item.get("claim_evidence_id")
                ),
                None,
            )
            if previous:
                try:
                    evidence_result = self.unlink_evidence(
                        ClaimEvidenceUnlink(
                            claim_id=candidate["claim_id"],
                            evidence_id=previous["claim_evidence_id"],
                            actor=request.reviewer,
                            note=request.note or "Match candidate rejected after review.",
                        )
                    )
                except DocumentError:
                    evidence_result = None
        reviewed = self.store.review_match_candidate(
            {
                "id": f"claim-match-review://{uuid4().hex}",
                "project_id": candidate["project_id"],
                "candidate_id": candidate["id"],
                "decision": request.decision,
                "reviewer": request.reviewer,
                "relationship": request.relationship,
                "note": request.note,
                "claim_evidence_id": evidence_id,
            }
        )
        self.workspace._audit(
            candidate["project_id"],
            "claim.match_reviewed",
            "claim_match_candidate",
            candidate["id"],
            request.model_dump(),
            actor=request.reviewer,
        )
        return {"candidate": reviewed, "evidence": evidence_result}

    def evidence_options(
        self,
        project_id: str,
        evidence_type: str | None,
        query: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        self.workspace._require_project(project_id)
        return self.store.list_evidence_options(project_id, evidence_type, query, limit)

    def validate(self, claim_id: str) -> dict[str, Any]:
        claim = self._require_claim(claim_id)
        evidence = claim["evidence"]
        checks: list[dict[str, Any]] = []
        if any(item["relationship"] == "refutes" for item in evidence):
            status = "contradicted"
            checks.append({"check": "refuting_evidence", "passed": False})
        else:
            supporting = [item for item in evidence if item["relationship"] == "supports"]
            for item in supporting:
                checks.extend(self._evidence_checks(item, claim))
            if not supporting:
                status = "insufficient_evidence"
            elif checks and all(item["passed"] for item in checks):
                status = "verified"
            else:
                status = "partially_supported"
        validation = {
            "status": status,
            "checks": checks,
            "supporting_count": sum(item["relationship"] == "supports" for item in evidence),
            "refuting_count": sum(item["relationship"] == "refutes" for item in evidence),
            "validated_at": utc_now(),
        }
        self.store.set_validation(claim_id, status, validation)
        return validation

    def _evidence_checks(
        self, evidence: dict[str, Any], claim: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if evidence["evidence_type"] == "experiment_run":
            run = self.experiments.get_run(evidence["evidence_entity_id"])
            if not run:
                return [{"check": "run_exists", "passed": False}]
            checks = [
                {"check": "run_completed", "passed": run["status"] == "completed"},
                {"check": "commit_verified", "passed": bool(run["commit_entity_id"])},
                {"check": "metrics_present", "passed": bool(run["metrics"])},
                {
                    "check": "dataset_version_present",
                    "passed": bool(run["dataset_id"] and run["dataset_version"]),
                },
            ]
            checks.extend(self._numeric_checks(claim, run["metrics"]))
            return checks
        if evidence["evidence_type"] == "metric":
            metric = self.experiments.get_metric(evidence["evidence_entity_id"])
            checks = [{"check": "metric_exists", "passed": bool(metric)}]
            if metric:
                checks.extend(self._numeric_checks(claim, [metric]))
            return checks
        if evidence["evidence_type"] == "table_cell":
            cell = self.store.get_table_cell(evidence["evidence_entity_id"])
            relations = self.workspace.store.list_relations(
                claim["project_id"], entity_id=evidence["evidence_entity_id"], limit=500
            )
            reported = claim.get("metadata", {}).get("reported_values", [])
            return [
                {"check": "table_cell_exists", "passed": bool(cell)},
                {
                    "check": "table_cell_metric_confirmed",
                    "passed": any(
                        edge["source_entity_id"] == evidence["evidence_entity_id"]
                        and edge["predicate"] == "reports"
                        and edge["review_status"] == "confirmed"
                        for edge in relations
                    ),
                },
                {
                    "check": "reported_numeric_value_matches_table_cell",
                    "passed": bool(cell)
                    and (
                        not reported
                        or any(
                            numeric_value_matches(cell["value"], float(value)) for value in reported
                        )
                    ),
                    "cell_value": cell["value"] if cell else None,
                    "reported_values": reported,
                },
            ]
        if evidence["evidence_type"] == "figure":
            return [
                {
                    "check": "figure_exists",
                    "passed": self.workspace.store.entity_exists(evidence["evidence_entity_id"]),
                }
            ]
        if evidence["evidence_type"] == "metric_aggregation":
            aggregation = self.store.get_table_metric_aggregation(evidence["evidence_entity_id"])
            return [
                {"check": "metric_aggregation_exists", "passed": bool(aggregation)},
                {
                    "check": "metric_aggregation_matches_reported_value",
                    "passed": bool(aggregation) and aggregation["status"] == "verified",
                    "computed_value": aggregation["computed_value"] if aggregation else None,
                    "reported_value": aggregation["reported_value"] if aggregation else None,
                },
                {
                    "check": "metric_aggregation_has_multiple_runs",
                    "passed": bool(aggregation)
                    and len(aggregation["run_ids"]) >= 2
                    and aggregation["sample_count"] >= 2,
                    "sample_count": aggregation["sample_count"] if aggregation else 0,
                    "variance": aggregation["variance"] if aggregation else None,
                },
            ]
        return [{"check": f"{evidence['evidence_type']}_exists", "passed": True}]

    @staticmethod
    def _numeric_checks(
        claim: dict[str, Any], metrics: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        reported = [float(value) for value in claim.get("metadata", {}).get("reported_values", [])]
        if not reported:
            return []
        text = claim["content"].casefold()
        named = [metric for metric in metrics if str(metric["name"]).casefold() in text]
        checks: list[dict[str, Any]] = [
            {
                "check": "reported_metric_resolved",
                "passed": bool(named),
                "reported_values": reported,
                "matched_metrics": [metric["name"] for metric in named],
            }
        ]
        if not named:
            return checks
        matches = []
        percentage_claim = "%" in claim["content"]
        for metric in named:
            observed = float(metric["value"])
            variants = [observed, observed * 100] if percentage_claim else [observed]
            if any(
                math.isclose(reported_value, variant, rel_tol=1e-4, abs_tol=1e-9)
                for reported_value in reported
                for variant in variants
            ):
                matches.append({"metric": metric["name"], "observed": observed})
        checks.append(
            {
                "check": "reported_numeric_value_matches",
                "passed": bool(matches),
                "matches": matches,
            }
        )
        return checks

    def suggest_matches(
        self, project_id: str, claim_ids: list[str] | None = None
    ) -> dict[str, Any]:
        claims = self.store.list_claims(project_id)
        if claim_ids:
            allowed = set(claim_ids)
            claims = [item for item in claims if item["id"] in allowed]
        runs = [
            self.experiments.get_run(item["id"]) for item in self.experiments.list_runs(project_id)
        ]
        candidates = []
        for claim in claims:
            text = claim["content"].casefold()
            for run in (item for item in runs if item):
                score, signals = self._match_score(text, claim.get("metadata", {}), run)
                if score < 0.35:
                    continue
                token = hashlib.sha256(f"{claim['id']}\x1f{run['id']}".encode()).hexdigest()
                candidates.append(
                    self.store.upsert_match_candidate(
                        {
                            "id": f"claim-match://sha256:{token}",
                            "project_id": project_id,
                            "claim_id": claim["id"],
                            "run_id": run["id"],
                            "score": min(1.0, score),
                            "signals": signals,
                        }
                    )
                )
        return {"claims": len(claims), "runs": len(runs), "candidates": candidates}

    @staticmethod
    def _match_score(
        text: str, metadata: dict[str, Any], run: dict[str, Any]
    ) -> tuple[float, dict[str, Any]]:
        score = 0.0
        signals: dict[str, Any] = {}
        identifiers = [run.get("external_id"), run.get("display_key"), run.get("id")]
        matched_ids = [value for value in identifiers if value and str(value).casefold() in text]
        if matched_ids:
            score += 0.75
            signals["explicit_run_ids"] = matched_ids
        matched_metrics = []
        matched_values = []
        for metric in run.get("metrics", []):
            if metric["name"].casefold() in text:
                score += 0.25
                matched_metrics.append(metric["name"])
            variants = {str(metric["value"]), f"{metric['value']:.3f}", f"{metric['value']:.2f}"}
            if any(value in text for value in variants):
                score += 0.25
                matched_values.append(metric["value"])
        if matched_metrics:
            signals["metrics"] = matched_metrics
        if matched_values:
            signals["values"] = matched_values
        for key in ("dataset_id", "dataset_version", "commit_sha"):
            value = run.get(key)
            if value and str(value).casefold() in text:
                score += 0.15
                signals[key] = value
        reported = metadata.get("reported_values", [])
        if reported and any(
            abs(float(value) - float(metric["value"])) < 1e-9
            for value in reported
            for metric in run.get("metrics", [])
        ):
            score += 0.3
            signals["numeric_exact"] = True
        return score, signals

    def _content(self, request: DocumentIngestRequest) -> tuple[ParsedDocument, bytes, str]:
        if request.content is not None:
            content = request.content.strip()
            source_uri = f"inline://{uuid4().hex}"
            return self.adapter.inline(content, source_uri), content.encode(), "text/plain"
        path = Path(request.source or "").expanduser().resolve()
        allowed_roots = (
            *self.settings.allowed_local_roots,
            self.settings.data_dir / "document_uploads",
        )
        if not any(path.is_relative_to(root.resolve()) for root in allowed_roots):
            raise DocumentError("document source is outside allowed local roots")
        if not path.is_file():
            raise DocumentError("document source not found")
        try:
            parsed = self.adapter.parse(path)
        except (OSError, ValueError) as exc:
            raise DocumentError(str(exc)) from exc
        media_type = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".html": "text/html",
            ".htm": "text/html",
            ".md": "text/markdown",
            ".markdown": "text/markdown",
            ".txt": "text/plain",
        }.get(path.suffix.casefold(), "application/octet-stream")
        return parsed, path.read_bytes(), media_type

    @staticmethod
    def _claims(document_id: str, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        claims = []
        number = 0
        signal = re.compile(
            r"(?:\d+(?:\.\d+)?\s*%|提升|降低|增加|减少|优于|达到|improv|increase|decrease|outperform)",
            re.IGNORECASE,
        )
        for section in sections:
            sentences = re.split(r"(?<=[。！？.!?])\s+|\n+", section["content"])
            for sentence in sentences:
                text = sentence.strip().removeprefix("Claim:").removeprefix("结论：").strip()
                explicit = sentence.strip().startswith(("Claim:", "结论："))
                if len(text) < 12 or not (explicit or signal.search(text)):
                    continue
                number += 1
                claim_type = "performance" if signal.search(text) else "other"
                token = hashlib.sha256(
                    f"{document_id}\x1f{section['id']}\x1f{number}\x1f{text}".encode()
                ).hexdigest()
                values = [
                    float(value)
                    for value in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)(?:\s*%)?", text)
                ][:20]
                run_ids = re.findall(r"\b(?:RUN|R)[-_]?[A-Z0-9]{4,}\b", text, re.I)
                claims.append(
                    {
                        "id": f"claim://{document_id.removeprefix('document://')}/{token}",
                        "display_key": f"CLM-{token[:8].upper()}",
                        "section_id": section["id"],
                        "content": text[:8_000],
                        "claim_type": claim_type,
                        "extraction_method": "explicit_marker" if explicit else "numeric_rule_v1",
                        "extraction_confidence": 0.98 if explicit else 0.72,
                        "source_locator": section["source_locator"],
                        "metadata": {
                            "candidate_ordinal": number,
                            "reported_values": values,
                            "mentioned_run_ids": run_ids,
                        },
                    }
                )
        return claims

    def _require_document(self, document_id: str) -> dict[str, Any]:
        item = self.store.get_document(document_id)
        if not item:
            raise DocumentError("document not found")
        return item

    def _require_claim(self, claim_id: str) -> dict[str, Any]:
        item = self.store.get_claim(claim_id)
        if not item:
            raise DocumentError("claim not found")
        return item

    def _require_candidate(self, candidate_id: str) -> dict[str, Any]:
        item = self.store.get_match_candidate(candidate_id)
        if not item:
            raise DocumentError("claim match candidate not found")
        return item
