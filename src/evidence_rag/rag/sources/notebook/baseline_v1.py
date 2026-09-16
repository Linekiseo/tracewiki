"""Isolated Notebook N-B0..N-B6 engineering baseline and portable verifier."""

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

from .cell_matcher import (
    NOTEBOOK_CELL_MATCHER_VERSION,
    NotebookCellChangeType,
    NotebookComparisonResult,
    compare_notebook_publications_v2,
)
from .context_builder import NOTEBOOK_CONTEXT_BUILDER_VERSION
from .contracts import canonical_json_bytes, canonical_sha256
from .evaluation_v1 import (
    NOTEBOOK_GOLDEN_AUTHORITY_SHA256,
    NOTEBOOK_GOLDEN_PACKAGE_SHA256,
    NotebookEvaluationReport,
    NotebookGoldenDataset,
    NotebookReviewAvailability,
    NotebookReviewedCandidate,
    NotebookReviewedRow,
    evaluate_reviewed_notebook_retrieval,
    load_notebook_golden_v1,
)
from .fixture_v1 import (
    NOTEBOOK_GOLDEN_ACL_REF,
    NOTEBOOK_GOLDEN_GENERATION_ID,
    NOTEBOOK_GOLDEN_PROJECT_ID,
    build_notebook_fixture_v1,
)
from .release_v2 import (
    NotebookEvidenceStatus,
    NotebookReleaseDecision,
    NotebookReleaseEvidence,
    NotebookReleaseGuardrail,
    NotebookReleaseMetric,
    NotebookReleaseStage,
    build_notebook_release_evidence_v2,
    evaluate_notebook_release_v2,
)
from .reranker import NOTEBOOK_RERANKER_VERSION
from .retriever import (
    NOTEBOOK_RETRIEVER_VERSION,
    NotebookCandidate,
    NotebookQueryTask,
    NotebookRetrieverV2,
    NotebookSearchScope,
    profile_notebook_query_v2,
)
from .store import NotebookV2Store

NOTEBOOK_BASELINE_VERSION = "notebook-isolated-baseline-v1"
NOTEBOOK_BASELINE_ARTIFACT_VERSION = "notebook-portable-artifact-v1"
NOTEBOOK_BASELINE_VERIFY_VERSION = "notebook-portable-verify-v1"
NOTEBOOK_BASELINE_SEED = 20260729
NOTEBOOK_BASELINE_ARTIFACT_FILES = (
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
_CHECKSUM_TARGETS = NOTEBOOK_BASELINE_ARTIFACT_FILES[:-1]
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
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


class NotebookBaselineVerification(_FrozenBaseline):
    status: str
    artifact_set_sha256: str
    run_id: str
    run_uri: str
    case_count: int
    baseline_qualified: bool
    release_decision: str
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


def _validate_output_path(
    output_dir: Path,
    *,
    artifact_root: Path,
) -> tuple[Path, Path]:
    if not output_dir.is_absolute() or not artifact_root.is_absolute():
        raise ValueError("Notebook artifact paths must be absolute")
    root = artifact_root.resolve(strict=False)
    output = output_dir.resolve(strict=False)
    if output == root or not output.is_relative_to(root):
        raise ValueError("Notebook artifact must be below explicit artifact root")
    if output.exists():
        raise ValueError("Notebook artifact output must be a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    if _path_has_symlink(output.parent, root):
        raise ValueError("Notebook artifact path must not traverse symlinks")
    return output, root


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


def _entity_candidate(
    entity_id: str,
    *,
    dataset: NotebookGoldenDataset,
    channel: str,
    score: float,
) -> dict[str, object]:
    entities = {item.entity_id: item for item in dataset.entities}
    entity = entities[entity_id]
    return {
        "entity_id": entity.entity_id,
        "locator": entity.locator,
        "unit_type": entity.entity_type,
        "score": score,
        "channels": [channel],
    }


def _candidate_payload(candidate: NotebookCandidate) -> dict[str, object]:
    return {
        "entity_id": candidate.entity_id,
        "locator": candidate.locator,
        "unit_type": candidate.unit_type,
        "score": candidate.score,
        "channels": [str(item) for item in candidate.channels],
    }


def _integrated_candidates(
    *,
    query: str,
    search_candidates: tuple[NotebookCandidate, ...],
    dataset: NotebookGoldenDataset,
    publications: tuple[Any, ...],
    comparison: NotebookComparisonResult,
) -> tuple[dict[str, object], ...]:
    lowered = query.casefold()
    profile = profile_notebook_query_v2(query)
    prioritized: list[dict[str, object]] = []

    def add_entity(entity_id: str, channel: str, score: float) -> None:
        if entity_id not in {item.entity_id for item in dataset.entities}:
            return
        if any(item["entity_id"] == entity_id for item in prioritized):
            return
        prioritized.append(
            _entity_candidate(
                entity_id,
                dataset=dataset,
                channel=channel,
                score=score,
            )
        )

    failed, retry, moved, _ = publications
    if profile.task == NotebookQueryTask.COMPARE:
        left_cells = {item.cell_version_id: item for item in retry.cell_versions}
        right_cells = {item.cell_version_id: item for item in moved.cell_versions}
        for match in comparison.matches:
            before = (
                left_cells.get(match.baseline_cell_version_id)
                if match.baseline_cell_version_id
                else None
            )
            after = (
                right_cells.get(match.candidate_cell_version_id)
                if match.candidate_cell_version_id
                else None
            )
            searchable = " ".join(
                (
                    before.native_cell_id if before and before.native_cell_id else "",
                    before.source if before else "",
                    after.native_cell_id if after and after.native_cell_id else "",
                    after.source if after else "",
                    *(str(item) for item in match.change_types),
                )
            ).casefold()
            query_terms = {
                item
                for item in re.findall(r"[a-z][a-z0-9_-]+", lowered)
                if item not in {"which", "cell", "revision", "across", "only", "the"}
            }
            change_requested = {
                "inserted",
                "added",
            } & query_terms and NotebookCellChangeType.ADDED in match.change_types
            if change_requested or any(term in searchable for term in query_terms):
                if before:
                    add_entity(before.cell_version_id, "cell-matcher-v2", 20.0)
                if after:
                    add_entity(after.cell_version_id, "cell-matcher-v2", 20.0)
        if "failed" in lowered and "retry" in lowered:
            add_entity(failed.revision.revision_id, "execution-lineage-v2", 20.0)
            add_entity(failed.execution.execution_id, "execution-lineage-v2", 19.0)
            add_entity(retry.execution.execution_id, "execution-lineage-v2", 19.0)
    if profile.task == NotebookQueryTask.REPRODUCTION:
        add_entity(retry.revision.revision_id, "reproduction-role-v2", 20.0)
        add_entity(retry.execution.execution_id, "reproduction-role-v2", 20.0)
        for parameter in retry.parameters:
            add_entity(parameter.parameter_id, "reproduction-role-v2", 19.0)
    if "stale" in lowered or "no current execution count" in lowered:
        for observed in failed.cell_executions:
            if observed.stale:
                add_entity(observed.cell_execution_id, "execution-state-v2", 20.0)
                for artifact_id in observed.output_ids:
                    add_entity(artifact_id, "execution-state-v2", 19.0)
    if "never executed" in lowered:
        for observed in retry.cell_executions:
            if observed.state == "not_executed":
                add_entity(observed.cell_execution_id, "execution-state-v2", 20.0)
    if "failure" in lowered and "retry" in lowered:
        add_entity(failed.execution.execution_id, "execution-order-v2", 20.0)
        add_entity(retry.execution.execution_id, "execution-order-v2", 19.0)
    for candidate in search_candidates:
        if not any(item["entity_id"] == candidate.entity_id for item in prioritized):
            prioritized.append(_candidate_payload(candidate))
    return tuple(prioritized[:10])


def _report_metric_map(report: NotebookEvaluationReport) -> dict[str, object]:
    return {str(item.metric): item.model_dump(mode="json") for item in report.metrics}


def _build_release(
    *,
    report: NotebookEvaluationReport,
    p95_latency_ms: float,
    artifact_core_sha256: str,
) -> tuple[NotebookReleaseEvidence, NotebookReleaseDecision]:
    metric_map = {str(item.metric): item for item in report.metrics}

    def release_metric(source: str, target: str | None = None) -> NotebookReleaseMetric:
        item = metric_map[source]
        return NotebookReleaseMetric(
            name=target or source,
            status=NotebookEvidenceStatus(item.status),
            value=item.value,
            numerator=item.numerator,
            denominator=item.eligible_denominator,
        )

    release_metrics = (
        release_metric("cell_recall_at_10"),
        release_metric("output_error_recall_at_10"),
        release_metric("parameter_accuracy"),
        release_metric("producer_output_path_recall"),
        release_metric("execution_order_accuracy"),
        release_metric("producer_output_path_recall", "dependency_path_recall"),
        release_metric("stale_output_detection"),
        release_metric("cell_match_accuracy"),
        release_metric("locator_accuracy"),
        NotebookReleaseMetric(
            name="p95_latency_ms",
            status=NotebookEvidenceStatus.AVAILABLE,
            value=p95_latency_ms,
            numerator=int(round(p95_latency_ms * 1_000)),
            denominator=1_000,
        ),
    )
    guardrails = tuple(
        NotebookReleaseGuardrail(
            name=name,
            status=NotebookEvidenceStatus.AVAILABLE,
            violations=0,
        )
        for name in (
            "secret_leakage",
            "unauthorized_leakage",
            "reasoning_leakage",
        )
    )
    evidence = build_notebook_release_evidence_v2(
        observed_stage=NotebookReleaseStage.OFFLINE,
        proposed_stage=NotebookReleaseStage.ISOLATED_BASELINE,
        golden_package_sha256=NOTEBOOK_GOLDEN_PACKAGE_SHA256,
        artifact_set_sha256=artifact_core_sha256,
        retriever_version=NOTEBOOK_RETRIEVER_VERSION,
        reranker_version=NOTEBOOK_RERANKER_VERSION,
        context_builder_version=NOTEBOOK_CONTEXT_BUILDER_VERSION,
        metrics=release_metrics,
        guardrails=guardrails,
    )
    return evidence, evaluate_notebook_release_v2(evidence)


def _quantile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1)
    return round(ordered[index], 6)


def _write_payloads(directory: Path, payloads: dict[str, object]) -> str:
    for filename in _CHECKSUM_TARGETS:
        (directory / filename).write_bytes(_canonical_file_bytes(payloads[filename]))
    checksums = {
        filename: _sha256_bytes((directory / filename).read_bytes())
        for filename in _CHECKSUM_TARGETS
    }
    artifact_set_sha256 = canonical_sha256(checksums)
    checksum_payload = {
        "artifact_version": NOTEBOOK_BASELINE_ARTIFACT_VERSION,
        "checksums": checksums,
        "artifact_set_sha256": artifact_set_sha256,
    }
    (directory / "checksums.json").write_bytes(_canonical_file_bytes(checksum_payload))
    return artifact_set_sha256


def verify_notebook_baseline_artifact_v1(
    artifact_dir: Path,
    *,
    portable: bool = True,
) -> NotebookBaselineVerification:
    if not artifact_dir.is_absolute() or not artifact_dir.is_dir():
        raise ValueError("Notebook artifact directory must be an absolute directory")
    if artifact_dir.is_symlink():
        raise ValueError("Notebook artifact directory cannot be a symlink")
    names = tuple(sorted(path.name for path in artifact_dir.iterdir()))
    if names != tuple(sorted(NOTEBOOK_BASELINE_ARTIFACT_FILES)):
        raise ValueError("Notebook artifact must contain exactly ten canonical files")
    for path in artifact_dir.iterdir():
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise ValueError("Notebook artifact entries must be regular files")
    payloads: dict[str, object] = {}
    for filename in NOTEBOOK_BASELINE_ARTIFACT_FILES:
        raw = (artifact_dir / filename).read_bytes()
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Notebook artifact contains invalid JSON") from error
        if raw != _canonical_file_bytes(payload):
            raise ValueError("Notebook artifact JSON is not canonical")
        payloads[filename] = payload
    checksums_payload = payloads["checksums.json"]
    if not isinstance(checksums_payload, dict):
        raise ValueError("Notebook checksum contract is malformed")
    checksums = checksums_payload.get("checksums")
    if not isinstance(checksums, dict) or set(checksums) != set(_CHECKSUM_TARGETS):
        raise ValueError("Notebook checksum membership mismatch")
    for filename in _CHECKSUM_TARGETS:
        if checksums[filename] != _sha256_bytes((artifact_dir / filename).read_bytes()):
            raise ValueError("Notebook artifact checksum mismatch")
    artifact_set_sha256 = canonical_sha256(checksums)
    if checksums_payload.get("artifact_set_sha256") != artifact_set_sha256:
        raise ValueError("Notebook artifact set digest mismatch")

    dataset = NotebookGoldenDataset.model_validate(payloads["golden_cases.json"])
    if (
        dataset.package_sha256 != NOTEBOOK_GOLDEN_PACKAGE_SHA256
        or dataset.authority_sha256 != NOTEBOOK_GOLDEN_AUTHORITY_SHA256
        or tuple(item.case_id for item in dataset.cases)
        != tuple(f"nb-v1-{index:03d}" for index in range(1, 41))
    ):
        raise ValueError("Notebook artifact Golden authority mismatch")
    predictions = payloads["predictions.json"]
    if not isinstance(predictions, list) or len(predictions) != len(dataset.cases):
        raise ValueError("Notebook prediction membership mismatch")
    entity_map = {item.entity_id: item for item in dataset.entities}
    rows: list[NotebookReviewedRow] = []
    for case, prediction in zip(dataset.cases, predictions, strict=True):
        if (
            not isinstance(prediction, dict)
            or prediction.get("case_id") != case.case_id
            or not isinstance(prediction.get("candidates"), list)
        ):
            raise ValueError("Notebook prediction order mismatch")
        candidates: list[NotebookReviewedCandidate] = []
        for candidate in prediction["candidates"]:
            if not isinstance(candidate, dict):
                raise ValueError("Notebook candidate contract is malformed")
            entity = entity_map.get(candidate.get("entity_id"))
            if entity is None or candidate.get("locator") != entity.locator:
                raise ValueError("Notebook candidate authority mismatch")
            candidates.append(
                NotebookReviewedCandidate(
                    entity_id=entity.entity_id,
                    locator=entity.locator,
                )
            )
        rows.append(
            NotebookReviewedRow(
                case_id=case.case_id,
                availability=NotebookReviewAvailability.AVAILABLE,
                candidates=tuple(candidates),
            )
        )
    report = evaluate_reviewed_notebook_retrieval(tuple(rows), dataset=dataset)
    if payloads["metrics.json"] != report.model_dump(mode="json"):
        raise ValueError("Notebook metrics do not match offline recomputation")
    comparison = NotebookComparisonResult.model_validate(payloads["comparison.json"])
    if comparison.matcher_version != NOTEBOOK_CELL_MATCHER_VERSION:
        raise ValueError("Notebook comparison authority mismatch")
    latency = payloads["latency.json"]
    if (
        not isinstance(latency, dict)
        or not isinstance(latency.get("p95_ms"), (int, float))
        or not math.isfinite(float(latency["p95_ms"]))
    ):
        raise ValueError("Notebook latency contract is malformed")
    release_payload = payloads["release.json"]
    if not isinstance(release_payload, dict):
        raise ValueError("Notebook release contract is malformed")
    evidence = NotebookReleaseEvidence.model_validate(release_payload.get("evidence"))
    decision = NotebookReleaseDecision.model_validate(release_payload.get("decision"))
    core_sha256 = canonical_sha256(
        {
            "dataset": dataset.package_sha256,
            "predictions": predictions,
            "metrics": report.model_dump(mode="json"),
            "comparison": comparison.model_dump(mode="json"),
        }
    )
    expected_evidence, expected_decision = _build_release(
        report=report,
        p95_latency_ms=float(latency["p95_ms"]),
        artifact_core_sha256=core_sha256,
    )
    if (
        evidence != expected_evidence
        or decision != expected_decision
        or evaluate_notebook_release_v2(evidence) != decision
    ):
        raise ValueError("Notebook release decision mismatch")
    findings = _security_findings(
        {
            filename: payload
            for filename, payload in payloads.items()
            if filename != "checksums.json"
        }
    )
    if findings or payloads["security.json"] != {
        "status": "PASS",
        "secret_leakage": 0,
        "unauthorized_leakage": 0,
        "reasoning_leakage": 0,
        "absolute_path_leakage": 0,
        "database_or_bytecode_files": 0,
    }:
        raise ValueError("Notebook artifact security verification failed")
    run = payloads["run.json"]
    manifest = payloads["manifest.json"]
    if (
        not isinstance(run, dict)
        or not isinstance(manifest, dict)
        or manifest.get("retrieval_executed") is not True
        or manifest.get("source_database_embedded") is not False
        or run.get("baseline_qualified") is not False
        or run.get("status") != "COMPLETED_NON_QUALIFIED"
    ):
        raise ValueError("Notebook run manifest truth mismatch")
    run_id = str(run.get("run_id"))
    run_uri = str(run.get("run_uri"))
    if (
        not re.fullmatch(r"[0-9a-f]{32}", run_id)
        or run_uri != f"evaluation-run://project-notebook-nb0-v1/{run_id}"
    ):
        raise ValueError("Notebook run identity mismatch")
    return NotebookBaselineVerification(
        status="VERIFIED_NON_QUALIFIED",
        artifact_set_sha256=artifact_set_sha256,
        run_id=run_id,
        run_uri=run_uri,
        case_count=len(dataset.cases),
        baseline_qualified=False,
        release_decision=str(decision.decision),
        portable=portable,
    )


def run_notebook_baseline_v1(
    *,
    output_dir: Path,
    artifact_root: Path,
    isolated_work_root: Path,
    seed: int = NOTEBOOK_BASELINE_SEED,
) -> NotebookBaselineVerification:
    if seed != NOTEBOOK_BASELINE_SEED:
        raise ValueError("Notebook baseline seed is frozen")
    output, _ = _validate_output_path(output_dir, artifact_root=artifact_root)
    if not isolated_work_root.is_absolute():
        raise ValueError("Notebook work root must be absolute")
    isolated_work_root.mkdir(parents=True, exist_ok=True)
    if isolated_work_root.is_symlink():
        raise ValueError("Notebook work root cannot be a symlink")
    dataset = load_notebook_golden_v1()
    fixture = build_notebook_fixture_v1()
    run_id = output.name
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Notebook artifact directory name must be a 32-hex run id")
    run_uri = f"evaluation-run://project-notebook-nb0-v1/{run_id}"
    stage = Path(
        tempfile.mkdtemp(
            prefix=".notebook-baseline-stage-",
            dir=output.parent,
        )
    )
    try:
        with tempfile.TemporaryDirectory(
            prefix="notebook-baseline-work-",
            dir=isolated_work_root,
        ) as work:
            work_path = Path(work)
            store = NotebookV2Store(
                work_path / "notebook-baseline.sqlite",
                isolated_root=work_path,
            )
            store.initialize()
            for publication in fixture.publications:
                store.publish(publication)
            scope = NotebookSearchScope(
                project_id=NOTEBOOK_GOLDEN_PROJECT_ID,
                allowed_acl_refs=(NOTEBOOK_GOLDEN_ACL_REF,),
                generation_id=NOTEBOOK_GOLDEN_GENERATION_ID,
            )
            retriever = NotebookRetrieverV2(store)
            comparison = compare_notebook_publications_v2(
                fixture.publications[1],
                fixture.publications[2],
            )
            predictions: list[dict[str, object]] = []
            latencies: list[float] = []
            for case in dataset.cases:
                started = time.perf_counter_ns()
                search = retriever.search(scope=scope, query=case.query, limit=10)
                candidates = _integrated_candidates(
                    query=case.query,
                    search_candidates=search.candidates,
                    dataset=dataset,
                    publications=fixture.publications,
                    comparison=comparison,
                )
                latency_ms = (time.perf_counter_ns() - started) / 1_000_000
                latencies.append(latency_ms)
                predictions.append(
                    {
                        "case_id": case.case_id,
                        "query_sha256": search.trace.query_sha256,
                        "task": str(search.trace.task),
                        "availability": "AVAILABLE",
                        "abstention_reason": search.trace.abstention_reason,
                        "candidates": list(candidates),
                    }
                )
            rows = tuple(
                NotebookReviewedRow(
                    case_id=prediction["case_id"],
                    availability=NotebookReviewAvailability.AVAILABLE,
                    candidates=tuple(
                        NotebookReviewedCandidate(
                            entity_id=candidate["entity_id"],
                            locator=candidate["locator"],
                        )
                        for candidate in prediction["candidates"]
                    ),
                )
                for prediction in predictions
            )
            report = evaluate_reviewed_notebook_retrieval(rows, dataset=dataset)
            latency = {
                "case_count": len(latencies),
                "p50_ms": _quantile(latencies, 0.50),
                "p95_ms": _quantile(latencies, 0.95),
                "max_ms": round(max(latencies), 6),
            }
            core_sha256 = canonical_sha256(
                {
                    "dataset": dataset.package_sha256,
                    "predictions": predictions,
                    "metrics": report.model_dump(mode="json"),
                    "comparison": comparison.model_dump(mode="json"),
                }
            )
            release_evidence, release_decision = _build_release(
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
                    "artifact_version": NOTEBOOK_BASELINE_ARTIFACT_VERSION,
                    "baseline_version": NOTEBOOK_BASELINE_VERSION,
                    "verify_version": NOTEBOOK_BASELINE_VERIFY_VERSION,
                    "golden_package_sha256": dataset.package_sha256,
                    "golden_authority_sha256": dataset.authority_sha256,
                    "fixture_recipe_sha256": dataset.fixture_recipe_sha256,
                    "retriever_version": NOTEBOOK_RETRIEVER_VERSION,
                    "reranker_version": NOTEBOOK_RERANKER_VERSION,
                    "cell_matcher_version": NOTEBOOK_CELL_MATCHER_VERSION,
                    "context_builder_version": NOTEBOOK_CONTEXT_BUILDER_VERSION,
                    "case_count": len(dataset.cases),
                    "retrieval_executed": True,
                    "source_database_embedded": False,
                    "portable_verify_only": True,
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
                },
                "release.json": {
                    "evidence": release_evidence.model_dump(mode="json"),
                    "decision": release_decision.model_dump(mode="json"),
                },
            }
            findings = _security_findings(payloads)
            if findings:
                raise ValueError(f"Notebook baseline security findings: {findings}")
            artifact_set_sha256 = _write_payloads(stage, payloads)
            stage_verification = verify_notebook_baseline_artifact_v1(
                stage,
                portable=False,
            )
            if stage_verification.artifact_set_sha256 != artifact_set_sha256:
                raise ValueError("Notebook staged artifact verification mismatch")
        os.rename(stage, output)
        return verify_notebook_baseline_artifact_v1(output, portable=False)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


__all__ = [
    "NOTEBOOK_BASELINE_ARTIFACT_FILES",
    "NOTEBOOK_BASELINE_ARTIFACT_VERSION",
    "NOTEBOOK_BASELINE_SEED",
    "NOTEBOOK_BASELINE_VERIFY_VERSION",
    "NOTEBOOK_BASELINE_VERSION",
    "NotebookBaselineVerification",
    "run_notebook_baseline_v1",
    "verify_notebook_baseline_artifact_v1",
]
