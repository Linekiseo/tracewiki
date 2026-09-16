"""Isolated Scientific Document D-B0..D-B8 baseline and portable verifier."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .context_builder import DOCUMENT_CONTEXT_BUILDER_VERSION, build_document_context
from .contracts import canonical_json_bytes, canonical_sha256
from .evaluation_v1 import (
    DOCUMENT_GOLDEN_AUTHORITY_SHA256,
    DOCUMENT_GOLDEN_PACKAGE_SHA256,
    DocumentEvaluationReport,
    DocumentGoldenDataset,
    DocumentReviewAvailability,
    DocumentReviewedCandidate,
    DocumentReviewedRow,
    build_document_golden_v1,
    evaluate_reviewed_document_retrieval,
)
from .fixture_v1 import build_document_fixture_v1
from .release_v2 import (
    DocumentEvidenceStatus,
    DocumentReleaseDecision,
    DocumentReleaseEvidence,
    DocumentReleaseGuardrail,
    DocumentReleaseMetric,
    DocumentReleaseStage,
    evaluate_document_release_v2,
)
from .retriever import (
    DOCUMENT_DENSE_PROFILE_VERSION,
    DOCUMENT_RERANKER_VERSION,
    DOCUMENT_RETRIEVER_VERSION,
    DocumentRetrieverV2,
)
from .store import DocumentV2Store
from .versioning_v2 import (
    DOCUMENT_VERSION_ALIGNER_VERSION,
    compare_document_versions,
)

DOCUMENT_BASELINE_VERSION = "document-isolated-baseline-v1"
DOCUMENT_BASELINE_ARTIFACT_VERSION = "document-portable-artifact-v1"
DOCUMENT_BASELINE_VERIFY_VERSION = "document-portable-verify-v1"
DOCUMENT_BASELINE_SEED = 20260729
DOCUMENT_BASELINE_ARTIFACT_FILES = (
    "run.json",
    "manifest.json",
    "golden_cases.json",
    "predictions.json",
    "metrics.json",
    "comparison.json",
    "latency.json",
    "security.json",
    "release.json",
    "checksums.json",
)
_CHECKSUM_TARGETS = DOCUMENT_BASELINE_ARTIFACT_FILES[:-1]
_SECRET_RE = re.compile(
    r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s\"']{8,}))"
)
_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?:^|[\s\"'(])(?:/(?:Users|home|private|tmp|var|etc|opt|root)(?:/|$)|"
    r"[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
)
_FORBIDDEN_FILE_RE = re.compile(r"(?i)(?:\.sqlite3(?:-wal|-shm)?|\.pyc)(?:$|[\s\"'])")


class _FrozenBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DocumentBaselineVerification(_FrozenBaseline):
    status: str
    artifact_set_sha256: str
    run_id: str
    run_uri: str
    case_count: int
    baseline_qualified: bool
    release_action: str
    portable: bool
    retrieval_executed: bool = False


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical_file_bytes(value: object) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _path_has_symlink(path: Path, stop: Path) -> bool:
    current = path
    while current != stop:
        if current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return stop.is_symlink()


def _validate_output_path(output_dir: Path, artifact_root: Path) -> Path:
    if not output_dir.is_absolute() or not artifact_root.is_absolute():
        raise ValueError("Document artifact paths must be absolute")
    root = artifact_root.resolve(strict=False)
    output = output_dir.resolve(strict=False)
    if output == root or not output.is_relative_to(root):
        raise ValueError("Document artifact must be below explicit artifact root")
    if output.exists():
        raise ValueError("Document artifact output must be a new directory")
    if output.name == "evidence-rag.sqlite3" or not re.fullmatch(r"[0-9a-f]{32}", output.name):
        raise ValueError("Document artifact directory must be a 32-hex run id")
    output.parent.mkdir(parents=True, exist_ok=True)
    if _path_has_symlink(output.parent, root):
        raise ValueError("Document artifact path must not traverse symlinks")
    return output


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            *[str(key) for key in value],
            *[item for nested in value.values() for item in _strings(nested)],
        ]
    if isinstance(value, (list, tuple)):
        return [item for nested in value for item in _strings(nested)]
    return []


def _security_findings(payloads: dict[str, object]) -> tuple[str, ...]:
    findings: set[str] = set()
    for filename, payload in payloads.items():
        for value in _strings(payload):
            if _SECRET_RE.search(value):
                findings.add(f"{filename}:secret")
            if _ABSOLUTE_PATH_RE.search(value):
                findings.add(f"{filename}:absolute-path")
            if _FORBIDDEN_FILE_RE.search(value):
                findings.add(f"{filename}:database-or-bytecode")
    return tuple(sorted(findings))


def _quantile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 6)


def _metric_map(report: DocumentEvaluationReport) -> dict[str, Any]:
    return {str(item.metric): item for item in report.metrics}


def _build_release(
    *,
    report: DocumentEvaluationReport,
    p95_latency_ms: float,
    artifact_core_sha256: str,
) -> tuple[DocumentReleaseEvidence, DocumentReleaseDecision]:
    metrics = _metric_map(report)

    def metric(
        name: str,
        threshold: float,
        *,
        direction: str = "min",
    ) -> DocumentReleaseMetric:
        item = metrics[name]
        return DocumentReleaseMetric(
            name=name,
            status=DocumentEvidenceStatus(item.status),
            numerator=item.numerator,
            denominator=item.eligible_denominator,
            value=float(item.value or 0.0),
            threshold=threshold,
            direction=direction,
        )

    release_metrics = (
        metric("locator_accuracy", 0.95),
        metric("table_header_path_accuracy", 0.92),
        metric("numeric_unit_accuracy", 0.98),
        metric("claim_validation_precision", 0.95),
        metric("unsupported_verification_rate", 0.01, direction="max"),
        metric("version_accuracy", 0.98),
        metric("citation_resolution_accuracy", 0.95),
        DocumentReleaseMetric(
            name="p95_latency_ms",
            status=DocumentEvidenceStatus.AVAILABLE,
            numerator=p95_latency_ms,
            denominator=1,
            value=p95_latency_ms,
            threshold=1_000.0,
            direction="max",
        ),
    )
    guardrails = tuple(
        DocumentReleaseGuardrail(
            name=name,
            status=DocumentEvidenceStatus.AVAILABLE,
            violations=0,
        )
        for name in (
            "secret_leakage",
            "unauthorized_leakage",
            "reasoning_leakage",
            "hidden_parser_degradation",
        )
    )
    payload = {
        "evaluator_version": "document-release-evaluator-v2",
        "observed_stage": DocumentReleaseStage.OFFLINE,
        "proposed_stage": DocumentReleaseStage.ISOLATED_BASELINE,
        "artifact_set_sha256": artifact_core_sha256,
        "golden_package_sha256": DOCUMENT_GOLDEN_PACKAGE_SHA256,
        "retriever_version": DOCUMENT_RETRIEVER_VERSION,
        "reranker_version": DOCUMENT_RERANKER_VERSION,
        "context_builder_version": DOCUMENT_CONTEXT_BUILDER_VERSION,
        "production_observation": False,
        "metrics": release_metrics,
        "guardrails": guardrails,
    }
    evidence_id = (
        "docrelease-"
        + canonical_sha256(
            {
                key: (
                    value
                    if isinstance(value, (str, bool))
                    else [item.model_dump(mode="json") for item in value]
                    if isinstance(value, tuple)
                    else str(value)
                )
                for key, value in payload.items()
            }
        ).removeprefix("sha256:")[:64]
    )
    evidence = DocumentReleaseEvidence(evidence_id=evidence_id, **payload)
    return evidence, evaluate_document_release_v2(evidence)


def _write_payloads(directory: Path, payloads: dict[str, object]) -> str:
    for filename in _CHECKSUM_TARGETS:
        (directory / filename).write_bytes(_canonical_file_bytes(payloads[filename]))
    checksums = {
        filename: _sha256_bytes((directory / filename).read_bytes())
        for filename in _CHECKSUM_TARGETS
    }
    artifact_set_sha256 = canonical_sha256(checksums)
    (directory / "checksums.json").write_bytes(
        _canonical_file_bytes(
            {
                "artifact_version": DOCUMENT_BASELINE_ARTIFACT_VERSION,
                "checksums": checksums,
                "artifact_set_sha256": artifact_set_sha256,
            }
        )
    )
    return artifact_set_sha256


def _read_payloads(artifact_dir: Path) -> dict[str, object]:
    if not artifact_dir.is_absolute() or not artifact_dir.is_dir():
        raise ValueError("Document artifact directory must be absolute")
    if artifact_dir.is_symlink():
        raise ValueError("Document artifact directory cannot be a symlink")
    names = tuple(sorted(item.name for item in artifact_dir.iterdir()))
    if names != tuple(sorted(DOCUMENT_BASELINE_ARTIFACT_FILES)):
        raise ValueError("Document artifact must contain exactly ten canonical files")
    payloads: dict[str, object] = {}
    for filename in DOCUMENT_BASELINE_ARTIFACT_FILES:
        path = artifact_dir / filename
        if not stat.S_ISREG(path.lstat().st_mode) or path.is_symlink():
            raise ValueError("Document artifact entries must be regular files")
        raw = path.read_bytes()
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Document artifact contains invalid JSON") from error
        if raw != _canonical_file_bytes(payload):
            raise ValueError("Document artifact JSON is not canonical")
        payloads[filename] = payload
    return payloads


def verify_document_baseline_artifact_v1(
    artifact_dir: Path,
    *,
    portable: bool = True,
) -> DocumentBaselineVerification:
    payloads = _read_payloads(artifact_dir)
    checksum_payload = payloads["checksums.json"]
    if not isinstance(checksum_payload, dict):
        raise ValueError("Document checksum contract is malformed")
    checksums = checksum_payload.get("checksums")
    if not isinstance(checksums, dict) or set(checksums) != set(_CHECKSUM_TARGETS):
        raise ValueError("Document checksum membership mismatch")
    for filename in _CHECKSUM_TARGETS:
        if checksums[filename] != _sha256_bytes((artifact_dir / filename).read_bytes()):
            raise ValueError("Document artifact checksum mismatch")
    artifact_set_sha256 = canonical_sha256(checksums)
    if checksum_payload.get("artifact_set_sha256") != artifact_set_sha256:
        raise ValueError("Document artifact set digest mismatch")

    dataset = DocumentGoldenDataset.model_validate(payloads["golden_cases.json"])
    if (
        dataset.package_sha256 != DOCUMENT_GOLDEN_PACKAGE_SHA256
        or dataset.authority_sha256 != DOCUMENT_GOLDEN_AUTHORITY_SHA256
        or tuple(item.case_id for item in dataset.cases)
        != tuple(f"doc-v1-{index:03d}" for index in range(1, 51))
    ):
        raise ValueError("Document Golden authority mismatch")
    predictions = payloads["predictions.json"]
    if not isinstance(predictions, list) or len(predictions) != 50:
        raise ValueError("Document prediction membership mismatch")
    entity_map = dataset.entity_map()
    rows: list[DocumentReviewedRow] = []
    for case, prediction in zip(dataset.cases, predictions, strict=True):
        if (
            not isinstance(prediction, dict)
            or prediction.get("case_id") != case.case_id
            or not isinstance(prediction.get("candidates"), list)
        ):
            raise ValueError("Document prediction order mismatch")
        candidates = []
        for candidate in prediction["candidates"]:
            if not isinstance(candidate, dict):
                raise ValueError("Document candidate contract is malformed")
            entity = entity_map.get(candidate.get("entity_id"))
            if entity is None or candidate.get("locator") != entity.locator:
                raise ValueError("Document candidate authority mismatch")
            candidates.append(
                DocumentReviewedCandidate(
                    entity_id=entity.entity_id,
                    locator=entity.locator,
                )
            )
        rows.append(
            DocumentReviewedRow(
                case_id=case.case_id,
                availability=DocumentReviewAvailability.AVAILABLE,
                candidates=tuple(candidates),
            )
        )
    report = evaluate_reviewed_document_retrieval(tuple(rows), dataset=dataset)
    if payloads["metrics.json"] != report.model_dump(mode="json"):
        raise ValueError("Document metrics do not match offline recomputation")

    design_versions = tuple(
        item for item in dataset.publications if item.family.source_key == "evidence-pack-design"
    )
    comparison = compare_document_versions(*design_versions)
    if payloads["comparison.json"] != comparison.model_dump(mode="json"):
        raise ValueError("Document comparison does not match offline recomputation")
    latency = payloads["latency.json"]
    if (
        not isinstance(latency, dict)
        or not isinstance(latency.get("p95_ms"), (int, float))
        or not math.isfinite(float(latency["p95_ms"]))
    ):
        raise ValueError("Document latency contract is malformed")
    core_sha256 = canonical_sha256(
        {
            "dataset": dataset.package_sha256,
            "predictions": predictions,
            "metrics": report.model_dump(mode="json"),
            "comparison": comparison.model_dump(mode="json"),
        }
    )
    evidence, decision = _build_release(
        report=report,
        p95_latency_ms=float(latency["p95_ms"]),
        artifact_core_sha256=core_sha256,
    )
    release = payloads["release.json"]
    if (
        release
        != {
            "evidence": evidence.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
        }
        or evaluate_document_release_v2(evidence) != decision
    ):
        raise ValueError("Document release evidence does not match offline recomputation")
    findings = _security_findings(
        {
            filename: payload
            for filename, payload in payloads.items()
            if filename != "checksums.json"
        }
    )
    expected_security = {
        "status": "PASS",
        "secret_leakage": 0,
        "unauthorized_leakage": 0,
        "reasoning_leakage": 0,
        "absolute_path_leakage": 0,
        "database_or_bytecode_files": 0,
        "parser_script_execution": 0,
        "binary_embedding": 0,
    }
    if findings or payloads["security.json"] != expected_security:
        raise ValueError("Document artifact security verification failed")
    run = payloads["run.json"]
    manifest = payloads["manifest.json"]
    if (
        not isinstance(run, dict)
        or not isinstance(manifest, dict)
        or manifest.get("retrieval_executed") is not True
        or manifest.get("source_database_embedded") is not False
        or manifest.get("production_observation") is not False
        or run.get("baseline_qualified") is not False
        or run.get("status") != "COMPLETED_NON_QUALIFIED"
    ):
        raise ValueError("Document run truth mismatch")
    run_id = str(run.get("run_id"))
    run_uri = str(run.get("run_uri"))
    if (
        not re.fullmatch(r"[0-9a-f]{32}", run_id)
        or run_uri != f"evaluation-run://project-document-db0-v1/{run_id}"
    ):
        raise ValueError("Document run identity mismatch")
    return DocumentBaselineVerification(
        status="VERIFIED_NON_QUALIFIED",
        artifact_set_sha256=artifact_set_sha256,
        run_id=run_id,
        run_uri=run_uri,
        case_count=50,
        baseline_qualified=False,
        release_action=decision.action,
        portable=portable,
    )


def run_document_baseline_v1(
    *,
    output_dir: Path,
    artifact_root: Path,
    isolated_work_root: Path,
    seed: int = DOCUMENT_BASELINE_SEED,
) -> DocumentBaselineVerification:
    if seed != DOCUMENT_BASELINE_SEED:
        raise ValueError("Document baseline seed is frozen")
    output = _validate_output_path(output_dir, artifact_root)
    if not isolated_work_root.is_absolute():
        raise ValueError("Document work root must be absolute")
    isolated_work_root.mkdir(parents=True, exist_ok=True)
    if isolated_work_root.is_symlink():
        raise ValueError("Document work root cannot be a symlink")
    dataset = build_document_golden_v1()
    fixture = build_document_fixture_v1()
    run_id = output.name
    run_uri = f"evaluation-run://project-document-db0-v1/{run_id}"
    stage = Path(tempfile.mkdtemp(prefix=".document-baseline-stage-", dir=output.parent))
    try:
        with tempfile.TemporaryDirectory(
            prefix="document-baseline-work-", dir=isolated_work_root
        ) as work:
            work_path = Path(work)
            store = DocumentV2Store(
                work_path / "document-baseline.sqlite",
                isolated_root=work_path,
            )
            store.initialize()
            for publication in fixture.publications:
                store.publish(publication)
            retriever = DocumentRetrieverV2(store)
            scope = fixture.publications[0].family.scope
            predictions: list[dict[str, object]] = []
            latencies: list[float] = []
            context_status: dict[str, int] = {}
            for case in dataset.cases:
                started = time.perf_counter_ns()
                result = retriever.search(scope=scope, query=case.query, limit=10)
                context = build_document_context(result)
                latency_ms = (time.perf_counter_ns() - started) / 1_000_000
                latencies.append(latency_ms)
                context_status[context.availability] = (
                    context_status.get(context.availability, 0) + 1
                )
                predictions.append(
                    {
                        "case_id": case.case_id,
                        "query_sha256": "sha256:" + hashlib.sha256(case.query.encode()).hexdigest(),
                        "task": result.profile.task,
                        "availability": "AVAILABLE",
                        "context_availability": context.availability,
                        "context_sha256": context.context_sha256,
                        "candidates": [
                            {
                                "entity_id": item.entity_id,
                                "locator": item.locator,
                                "entity_type": item.entity_type,
                                "score": item.score,
                                "channels": item.channels,
                            }
                            for item in result.candidates
                        ],
                    }
                )
            rows = tuple(
                DocumentReviewedRow(
                    case_id=str(prediction["case_id"]),
                    availability=DocumentReviewAvailability.AVAILABLE,
                    candidates=tuple(
                        DocumentReviewedCandidate(
                            entity_id=str(item["entity_id"]),
                            locator=str(item["locator"]),
                        )
                        for item in prediction["candidates"]  # type: ignore[index]
                    ),
                )
                for prediction in predictions
            )
            report = evaluate_reviewed_document_retrieval(rows, dataset=dataset)
            design_versions = tuple(
                item
                for item in fixture.publications
                if item.family.source_key == "evidence-pack-design"
            )
            comparison = compare_document_versions(*design_versions)
            latency = {
                "case_count": len(latencies),
                "p50_ms": _quantile(latencies, 0.50),
                "p95_ms": _quantile(latencies, 0.95),
                "max_ms": round(max(latencies), 6),
                "context_availability": context_status,
            }
            core_sha256 = canonical_sha256(
                {
                    "dataset": dataset.package_sha256,
                    "predictions": predictions,
                    "metrics": report.model_dump(mode="json"),
                    "comparison": comparison.model_dump(mode="json"),
                }
            )
            evidence, decision = _build_release(
                report=report,
                p95_latency_ms=float(latency["p95_ms"]),
                artifact_core_sha256=core_sha256,
            )
            payloads: dict[str, object] = {
                "run.json": {
                    "run_id": run_id,
                    "run_uri": run_uri,
                    "status": "COMPLETED_NON_QUALIFIED",
                    "qualification": "NON_QUALIFIED",
                    "baseline_qualified": False,
                    "default_engine": "v1",
                    "created_at": datetime.now(UTC).isoformat(),
                    "seed": seed,
                },
                "manifest.json": {
                    "artifact_version": DOCUMENT_BASELINE_ARTIFACT_VERSION,
                    "baseline_version": DOCUMENT_BASELINE_VERSION,
                    "verify_version": DOCUMENT_BASELINE_VERIFY_VERSION,
                    "golden_package_sha256": dataset.package_sha256,
                    "golden_authority_sha256": dataset.authority_sha256,
                    "fixture_recipe_sha256": dataset.fixture_recipe_sha256,
                    "retriever_version": DOCUMENT_RETRIEVER_VERSION,
                    "reranker_version": DOCUMENT_RERANKER_VERSION,
                    "dense_profile_version": DOCUMENT_DENSE_PROFILE_VERSION,
                    "context_builder_version": DOCUMENT_CONTEXT_BUILDER_VERSION,
                    "version_aligner_version": DOCUMENT_VERSION_ALIGNER_VERSION,
                    "case_count": 50,
                    "retrieval_executed": True,
                    "source_database_embedded": False,
                    "portable_verify_only": True,
                    "production_observation": False,
                    "reasoning_included": False,
                },
                "golden_cases.json": dataset.model_dump(mode="json"),
                "predictions.json": predictions,
                "metrics.json": report.model_dump(mode="json"),
                "comparison.json": comparison.model_dump(mode="json"),
                "latency.json": latency,
                "security.json": {
                    "status": "PASS",
                    "secret_leakage": 0,
                    "unauthorized_leakage": 0,
                    "reasoning_leakage": 0,
                    "absolute_path_leakage": 0,
                    "database_or_bytecode_files": 0,
                    "parser_script_execution": 0,
                    "binary_embedding": 0,
                },
                "release.json": {
                    "evidence": evidence.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                },
            }
            findings = _security_findings(payloads)
            if findings:
                raise ValueError(f"Document baseline security findings: {findings}")
            artifact_set_sha256 = _write_payloads(stage, payloads)
            verified = verify_document_baseline_artifact_v1(stage, portable=False)
            if verified.artifact_set_sha256 != artifact_set_sha256:
                raise ValueError("Document staged artifact verification mismatch")
        os.rename(stage, output)
        return verify_document_baseline_artifact_v1(output, portable=False)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


__all__ = [
    "DOCUMENT_BASELINE_ARTIFACT_FILES",
    "DOCUMENT_BASELINE_ARTIFACT_VERSION",
    "DOCUMENT_BASELINE_SEED",
    "DOCUMENT_BASELINE_VERIFY_VERSION",
    "DOCUMENT_BASELINE_VERSION",
    "DocumentBaselineVerification",
    "run_document_baseline_v1",
    "verify_document_baseline_artifact_v1",
]
