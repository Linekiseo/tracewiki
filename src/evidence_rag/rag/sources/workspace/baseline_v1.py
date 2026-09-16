"""Isolated Workspace W-B0..W-B8 baseline and portable verifier."""

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

from .context_builder import WORKSPACE_CONTEXT_BUILDER_VERSION, build_workspace_context
from .contracts import (
    WorkspaceEntityType,
    canonical_json_bytes,
    canonical_sha256,
)
from .evaluation_v1 import (
    WORKSPACE_GOLDEN_AUTHORITY_SHA256,
    WORKSPACE_GOLDEN_PACKAGE_SHA256,
    WorkspaceEvaluationReport,
    WorkspaceGoldenDataset,
    WorkspaceReviewAvailability,
    WorkspaceReviewedCandidate,
    WorkspaceReviewedRow,
    build_workspace_golden_v1,
    evaluate_reviewed_workspace_retrieval,
)
from .evidence_v2 import evaluate_workspace_coverage
from .fixture_v1 import build_workspace_fixture_v1
from .planning_v2 import dependency_path, evaluate_workspace_done_gate
from .release_v2 import (
    WorkspaceEvidenceStatus,
    WorkspaceReleaseDecision,
    WorkspaceReleaseEvidence,
    WorkspaceReleaseGuardrail,
    WorkspaceReleaseMetric,
    WorkspaceReleaseStage,
    build_workspace_release_evidence_v2,
    evaluate_workspace_release_v2,
)
from .retriever import (
    WORKSPACE_DENSE_PROFILE_VERSION,
    WORKSPACE_RERANKER_VERSION,
    WORKSPACE_RETRIEVER_VERSION,
    WorkspaceRetrieverV2,
)
from .store import WorkspaceV2Store

WORKSPACE_BASELINE_VERSION = "workspace-isolated-baseline-v1"
WORKSPACE_BASELINE_ARTIFACT_VERSION = "workspace-portable-artifact-v1"
WORKSPACE_BASELINE_VERIFY_VERSION = "workspace-portable-verify-v1"
WORKSPACE_BASELINE_SEED = 20260729
WORKSPACE_BASELINE_ARTIFACT_FILES = (
    "run.json",
    "manifest.json",
    "golden_cases.json",
    "predictions.json",
    "metrics.json",
    "planning.json",
    "latency.json",
    "security.json",
    "release.json",
    "checksums.json",
)
_CHECKSUM_TARGETS = WORKSPACE_BASELINE_ARTIFACT_FILES[:-1]
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


class WorkspaceBaselineVerification(_FrozenBaseline):
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
        if current.exists() and current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return stop.exists() and stop.is_symlink()


def _validate_output_path(output_dir: Path, artifact_root: Path) -> Path:
    if not output_dir.is_absolute() or not artifact_root.is_absolute():
        raise ValueError("Workspace artifact paths must be absolute")
    root = artifact_root.resolve(strict=False)
    output = output_dir.resolve(strict=False)
    if output == root or not output.is_relative_to(root):
        raise ValueError("Workspace artifact must be below explicit artifact root")
    if output.exists():
        raise ValueError("Workspace artifact output must be new")
    if not re.fullmatch(r"[0-9a-f]{32}", output.name):
        raise ValueError("Workspace artifact directory must be a 32-hex run id")
    output.parent.mkdir(parents=True, exist_ok=True)
    if _path_has_symlink(output.parent, root):
        raise ValueError("Workspace artifact path traverses a symlink")
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


def _planning_payload(dataset: WorkspaceGoldenDataset) -> dict[str, Any]:
    entities = dataset.entity_map()
    values = tuple(entities.values())
    requirements = tuple(
        item for item in values if item.entity_type == WorkspaceEntityType.EVIDENCE_REQUIREMENT
    )
    links = tuple(item for item in values if item.entity_type == WorkspaceEntityType.EVIDENCE_LINK)
    criteria = tuple(
        item for item in values if item.entity_type == WorkspaceEntityType.ACCEPTANCE_CRITERION
    )
    checks = tuple(
        item for item in values if item.entity_type == WorkspaceEntityType.ACCEPTANCE_CHECK
    )
    dependencies = tuple(
        item for item in values if item.entity_type == WorkspaceEntityType.DEPENDENCY
    )
    blockers = tuple(item for item in values if item.entity_type == WorkspaceEntityType.BLOCKER)
    coverage = tuple(
        evaluate_workspace_coverage(
            item,
            links,
            observed_at="2026-07-29T08:00:00Z",
            allowed_acl_refs=(dataset.publication.scope.acl_ref,),
            enforce_acl=True,
        )
        for item in requirements
    )
    done_gates = tuple(
        evaluate_workspace_done_gate(
            entities[work_id],
            criteria=criteria,
            checks=checks,
            dependencies=dependencies,
            blockers=blockers,
            evidence_links=links,
        )
        for work_id in ("work-04", "work-13")
    )
    path = dependency_path(
        dataset.publication.edges,
        start_id="work-01",
        target_id="work-03",
    )
    current_intelligence = next(
        item
        for item in values
        if item.entity_type == WorkspaceEntityType.INTELLIGENCE_RUN and item.current
    )
    snapshot = next(item for item in values if item.entity_type == WorkspaceEntityType.SNAPSHOT)
    return {
        "coverage": [item.model_dump(mode="json") for item in coverage],
        "done_gates": [item.model_dump(mode="json") for item in done_gates],
        "dependency_path": path.model_dump(mode="json"),
        "snapshot_id": snapshot.entity_id,
        "snapshot_sha256": snapshot.content_sha256,
        "intelligence_run_id": current_intelligence.entity_id,
        "intelligence_sha256": current_intelligence.content_sha256,
        "authoritative_state_mutated": False,
    }


def _metric_map(report: WorkspaceEvaluationReport) -> dict[str, Any]:
    return {item.metric.value: item for item in report.metrics}


def _build_release(
    *,
    report: WorkspaceEvaluationReport,
    p95_latency_ms: float,
    artifact_core_sha256: str,
) -> tuple[WorkspaceReleaseEvidence, WorkspaceReleaseDecision]:
    metrics = _metric_map(report)

    def metric(name: str) -> WorkspaceReleaseMetric:
        item = metrics[name]
        return WorkspaceReleaseMetric(
            name=name,
            status=WorkspaceEvidenceStatus(item.status),
            numerator=item.numerator,
            denominator=item.eligible_denominator,
            value=item.value,
        )

    release_metrics = tuple(
        metric(name)
        for name in (
            "scope_resolution_accuracy",
            "current_state_accuracy",
            "temporal_accuracy",
            "blocker_precision",
            "evidence_coverage_precision",
            "acceptance_gate_accuracy",
            "authority_classification_accuracy",
            "unsafe_mutation_rate",
            "acl_leakage_rate",
        )
    ) + (
        WorkspaceReleaseMetric(
            name="p95_latency_ms",
            status=WorkspaceEvidenceStatus.AVAILABLE,
            numerator=p95_latency_ms,
            denominator=1,
            value=p95_latency_ms,
        ),
    )
    guardrails = tuple(
        WorkspaceReleaseGuardrail(
            name=name,
            status=WorkspaceEvidenceStatus.AVAILABLE,
            violations=0,
        )
        for name in (
            "secret_leakage",
            "unauthorized_leakage",
            "reasoning_leakage",
            "unsafe_mutation",
        )
    )
    evidence = build_workspace_release_evidence_v2(
        observed_stage=WorkspaceReleaseStage.OFFLINE,
        proposed_stage=WorkspaceReleaseStage.ISOLATED_BASELINE,
        golden_package_sha256=WORKSPACE_GOLDEN_PACKAGE_SHA256,
        artifact_set_sha256=artifact_core_sha256,
        retriever_version=WORKSPACE_RETRIEVER_VERSION,
        reranker_version=WORKSPACE_RERANKER_VERSION,
        context_builder_version=WORKSPACE_CONTEXT_BUILDER_VERSION,
        metrics=release_metrics,
        guardrails=guardrails,
        production_observation=False,
    )
    return evidence, evaluate_workspace_release_v2(evidence)


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
                "artifact_version": WORKSPACE_BASELINE_ARTIFACT_VERSION,
                "checksums": checksums,
                "artifact_set_sha256": artifact_set_sha256,
            }
        )
    )
    return artifact_set_sha256


def _read_payloads(artifact_dir: Path) -> dict[str, object]:
    if not artifact_dir.is_absolute() or not artifact_dir.is_dir():
        raise ValueError("Workspace artifact directory must be absolute")
    if artifact_dir.is_symlink():
        raise ValueError("Workspace artifact directory cannot be a symlink")
    names = tuple(sorted(item.name for item in artifact_dir.iterdir()))
    if names != tuple(sorted(WORKSPACE_BASELINE_ARTIFACT_FILES)):
        raise ValueError("Workspace artifact must contain exactly ten canonical files")
    payloads: dict[str, object] = {}
    for filename in WORKSPACE_BASELINE_ARTIFACT_FILES:
        path = artifact_dir / filename
        if not stat.S_ISREG(path.lstat().st_mode) or path.is_symlink():
            raise ValueError("Workspace artifact entries must be regular files")
        raw = path.read_bytes()
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Workspace artifact contains invalid JSON") from error
        if raw != _canonical_file_bytes(payload):
            raise ValueError("Workspace artifact JSON is not canonical")
        payloads[filename] = payload
    return payloads


def verify_workspace_baseline_artifact_v1(
    artifact_dir: Path,
    *,
    portable: bool = True,
) -> WorkspaceBaselineVerification:
    payloads = _read_payloads(artifact_dir)
    checksum_payload = payloads["checksums.json"]
    if not isinstance(checksum_payload, dict):
        raise ValueError("Workspace checksum contract is malformed")
    checksums = checksum_payload.get("checksums")
    if not isinstance(checksums, dict) or set(checksums) != set(_CHECKSUM_TARGETS):
        raise ValueError("Workspace checksum membership mismatch")
    for filename in _CHECKSUM_TARGETS:
        if checksums[filename] != _sha256_bytes((artifact_dir / filename).read_bytes()):
            raise ValueError("Workspace artifact checksum mismatch")
    artifact_set_sha256 = canonical_sha256(checksums)
    if checksum_payload.get("artifact_set_sha256") != artifact_set_sha256:
        raise ValueError("Workspace artifact set digest mismatch")
    dataset = WorkspaceGoldenDataset.model_validate(payloads["golden_cases.json"])
    if (
        dataset.package_sha256 != WORKSPACE_GOLDEN_PACKAGE_SHA256
        or dataset.authority_sha256 != WORKSPACE_GOLDEN_AUTHORITY_SHA256
        or tuple(item.case_id for item in dataset.cases)
        != tuple(f"workspace-v1-{index:03d}" for index in range(1, 41))
    ):
        raise ValueError("Workspace Golden authority mismatch")
    predictions = payloads["predictions.json"]
    if not isinstance(predictions, list) or len(predictions) != 40:
        raise ValueError("Workspace prediction membership mismatch")
    entity_map = dataset.entity_map()
    rows: list[WorkspaceReviewedRow] = []
    for case, prediction in zip(dataset.cases, predictions, strict=True):
        if (
            not isinstance(prediction, dict)
            or prediction.get("case_id") != case.case_id
            or not isinstance(prediction.get("candidates"), list)
        ):
            raise ValueError("Workspace prediction order mismatch")
        candidates: list[WorkspaceReviewedCandidate] = []
        for candidate in prediction["candidates"]:
            if not isinstance(candidate, dict):
                raise ValueError("Workspace candidate contract is malformed")
            entity = entity_map.get(candidate.get("entity_id"))
            if entity is None or candidate.get("locator") != entity.locator:
                raise ValueError("Workspace candidate authority mismatch")
            candidates.append(
                WorkspaceReviewedCandidate(
                    entity_id=entity.entity_id,
                    locator=entity.locator,
                )
            )
        rows.append(
            WorkspaceReviewedRow(
                case_id=case.case_id,
                availability=WorkspaceReviewAvailability.AVAILABLE,
                candidates=tuple(candidates),
            )
        )
    report = evaluate_reviewed_workspace_retrieval(tuple(rows), dataset=dataset)
    if payloads["metrics.json"] != report.model_dump(mode="json"):
        raise ValueError("Workspace metrics do not match offline recomputation")
    planning = _planning_payload(dataset)
    if payloads["planning.json"] != planning:
        raise ValueError("Workspace planning evidence does not match frozen authority")
    latency = payloads["latency.json"]
    if (
        not isinstance(latency, dict)
        or not isinstance(latency.get("p95_ms"), (int, float))
        or not math.isfinite(float(latency["p95_ms"]))
    ):
        raise ValueError("Workspace latency contract is malformed")
    core_sha256 = canonical_sha256(
        {
            "dataset": dataset.package_sha256,
            "predictions": predictions,
            "metrics": report.model_dump(mode="json"),
            "planning": planning,
        }
    )
    evidence, decision = _build_release(
        report=report,
        p95_latency_ms=float(latency["p95_ms"]),
        artifact_core_sha256=core_sha256,
    )
    if payloads["release.json"] != {
        "evidence": evidence.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
    }:
        raise ValueError("Workspace release evidence mismatch")
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
        "unsafe_mutation": 0,
        "absolute_path_leakage": 0,
        "database_or_bytecode_files": 0,
    }
    if findings or payloads["security.json"] != expected_security:
        raise ValueError("Workspace artifact security verification failed")
    run = payloads["run.json"]
    manifest = payloads["manifest.json"]
    if (
        not isinstance(run, dict)
        or not isinstance(manifest, dict)
        or run.get("status") != "COMPLETED_NON_QUALIFIED"
        or run.get("baseline_qualified") is not False
        or manifest.get("retrieval_executed") is not True
        or manifest.get("source_database_embedded") is not False
        or manifest.get("production_observation") is not False
        or manifest.get("read_only") is not True
    ):
        raise ValueError("Workspace run truth mismatch")
    run_id = str(run.get("run_id"))
    run_uri = str(run.get("run_uri"))
    if (
        not re.fullmatch(r"[0-9a-f]{32}", run_id)
        or run_uri != f"evaluation-run://project-workspace-wb0-v1/{run_id}"
    ):
        raise ValueError("Workspace run identity mismatch")
    return WorkspaceBaselineVerification(
        status="VERIFIED_NON_QUALIFIED",
        artifact_set_sha256=artifact_set_sha256,
        run_id=run_id,
        run_uri=run_uri,
        case_count=40,
        baseline_qualified=False,
        release_decision=decision.decision,
        portable=portable,
    )


def run_workspace_baseline_v1(
    *,
    output_dir: Path,
    artifact_root: Path,
    isolated_work_root: Path,
    seed: int = WORKSPACE_BASELINE_SEED,
) -> WorkspaceBaselineVerification:
    if seed != WORKSPACE_BASELINE_SEED:
        raise ValueError("Workspace baseline seed is frozen")
    output = _validate_output_path(output_dir, artifact_root)
    if not isolated_work_root.is_absolute():
        raise ValueError("Workspace work root must be absolute")
    isolated_work_root.mkdir(parents=True, exist_ok=True)
    if isolated_work_root.is_symlink():
        raise ValueError("Workspace work root cannot be a symlink")
    dataset = build_workspace_golden_v1()
    fixture = build_workspace_fixture_v1()
    run_id = output.name
    run_uri = f"evaluation-run://project-workspace-wb0-v1/{run_id}"
    stage = Path(tempfile.mkdtemp(prefix=".workspace-baseline-stage-", dir=output.parent))
    try:
        with tempfile.TemporaryDirectory(
            prefix="workspace-baseline-work-",
            dir=isolated_work_root,
        ) as work:
            work_path = Path(work)
            store = WorkspaceV2Store(
                work_path / "workspace-baseline.sqlite",
                isolated_root=work_path,
            )
            store.publish(fixture.publication)
            retriever = WorkspaceRetrieverV2(store)
            predictions: list[dict[str, object]] = []
            latencies: list[float] = []
            context_status: dict[str, int] = {}
            for case in dataset.cases:
                started = time.perf_counter_ns()
                result = retriever.search(
                    scope=fixture.publication.scope,
                    query=case.query,
                    as_of=case.as_of,
                    limit=10,
                    allowed_acl_refs=(fixture.publication.scope.acl_ref,),
                    enforce_acl=True,
                )
                context = build_workspace_context(result)
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
                        "ambiguous_scope": result.ambiguous_scope,
                        "candidates": [
                            {
                                "entity_id": item.entity_id,
                                "locator": item.locator,
                                "entity_type": item.entity_type,
                                "authority": item.authority,
                                "score": item.score,
                                "channels": item.channels,
                            }
                            for item in result.candidates
                        ],
                    }
                )
            rows = tuple(
                WorkspaceReviewedRow(
                    case_id=str(prediction["case_id"]),
                    availability=WorkspaceReviewAvailability.AVAILABLE,
                    candidates=tuple(
                        WorkspaceReviewedCandidate(
                            entity_id=str(item["entity_id"]),
                            locator=str(item["locator"]),
                        )
                        for item in prediction["candidates"]  # type: ignore[index]
                    ),
                )
                for prediction in predictions
            )
            report = evaluate_reviewed_workspace_retrieval(rows, dataset=dataset)
            planning = _planning_payload(dataset)
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
                    "planning": planning,
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
                    "artifact_version": WORKSPACE_BASELINE_ARTIFACT_VERSION,
                    "baseline_version": WORKSPACE_BASELINE_VERSION,
                    "verify_version": WORKSPACE_BASELINE_VERIFY_VERSION,
                    "golden_package_sha256": dataset.package_sha256,
                    "golden_authority_sha256": dataset.authority_sha256,
                    "fixture_recipe_sha256": dataset.fixture_recipe_sha256,
                    "retriever_version": WORKSPACE_RETRIEVER_VERSION,
                    "reranker_version": WORKSPACE_RERANKER_VERSION,
                    "dense_profile_version": WORKSPACE_DENSE_PROFILE_VERSION,
                    "context_builder_version": WORKSPACE_CONTEXT_BUILDER_VERSION,
                    "case_count": 40,
                    "retrieval_executed": True,
                    "source_database_embedded": False,
                    "portable_verify_only": True,
                    "production_observation": False,
                    "read_only": True,
                    "reasoning_included": False,
                },
                "golden_cases.json": dataset.model_dump(mode="json"),
                "predictions.json": predictions,
                "metrics.json": report.model_dump(mode="json"),
                "planning.json": planning,
                "latency.json": latency,
                "security.json": {
                    "status": "PASS",
                    "secret_leakage": 0,
                    "unauthorized_leakage": 0,
                    "reasoning_leakage": 0,
                    "unsafe_mutation": 0,
                    "absolute_path_leakage": 0,
                    "database_or_bytecode_files": 0,
                },
                "release.json": {
                    "evidence": evidence.model_dump(mode="json"),
                    "decision": decision.model_dump(mode="json"),
                },
            }
            findings = _security_findings(payloads)
            if findings:
                raise ValueError(f"Workspace baseline security findings: {findings}")
            artifact_set_sha256 = _write_payloads(stage, payloads)
            verified = verify_workspace_baseline_artifact_v1(stage, portable=False)
            if verified.artifact_set_sha256 != artifact_set_sha256:
                raise ValueError("Workspace staged artifact verification mismatch")
        os.rename(stage, output)
        return verify_workspace_baseline_artifact_v1(output, portable=False)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


__all__ = [
    "WORKSPACE_BASELINE_ARTIFACT_FILES",
    "WORKSPACE_BASELINE_ARTIFACT_VERSION",
    "WORKSPACE_BASELINE_SEED",
    "WORKSPACE_BASELINE_VERIFY_VERSION",
    "WORKSPACE_BASELINE_VERSION",
    "WorkspaceBaselineVerification",
    "run_workspace_baseline_v1",
    "verify_workspace_baseline_artifact_v1",
]
