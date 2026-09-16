"""Immutable 120-case Wiki navigation quality evidence and offline verifier."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..global_governance_v2 import inspect_untrusted_content_v2
from .contracts_v1 import canonical_json_bytes_v1, canonical_sha256_v1
from .evaluation_v1 import (
    WIKI_EVALUATOR_VERSION,
    WIKI_GOLDEN_CASE_COUNT,
    WikiEvaluationReportV1,
    WikiMetricAvailabilityV1,
    WikiMetricV1,
    WikiReviewedNavigationRowV1,
    WikiSliceEvaluationReportV1,
    build_wiki_golden_release_v1,
    evaluate_reviewed_wiki_navigation_v1,
    evaluate_wiki_navigation_slices_v1,
)

WIKI_QUALITY_VERSION = "agent-native-wiki-quality-run-v1"
WIKI_QUALITY_ARTIFACT_VERSION = "wiki-navigation-quality-portable-artifact-v1"
WIKI_QUALITY_FILES = (
    "manifest.json",
    "golden.json",
    "rows.jsonl",
    "executions.jsonl",
    "overall.json",
    "slices.json",
    "security.json",
    "checksums.json",
)


class WikiQualityError(ValueError):
    """Raised when Wiki quality evidence is incomplete, unsafe, or inconsistent."""


class WikiQualityHardGateV1(StrEnum):
    ACL_SAFETY = "acl_safety"
    UNSUPPORTED_CLAIM_SAFETY = "unsupported_claim_safety"
    CORRECT_REFUSAL = "correct_refusal"
    COMPLETE_MEMBERSHIP = "complete_membership"
    NO_EXECUTION_ERRORS = "no_execution_errors"


class _FrozenQuality(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        allow_inf_nan=False,
    )

    def model_copy(
        self,
        *,
        update: dict[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(mode="python", round_trip=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)


class WikiQualityExecutionV1(_FrozenQuality):
    case_id: str
    duration_ns: int = Field(ge=1)
    navigation_sha256: str
    stop_reason: str
    search_count: int = Field(ge=0)
    page_read_count: int = Field(ge=0)
    raw_read_count: int = Field(ge=0)
    diagnostic_code: str | None
    content_sha256: str

    @model_validator(mode="after")
    def _execution(self) -> WikiQualityExecutionV1:
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiQualityError("Wiki quality execution digest mismatch")
        return self


class WikiQualityGateResultV1(_FrozenQuality):
    gate: WikiQualityHardGateV1
    passed: bool
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)

    @model_validator(mode="after")
    def _gate(self) -> WikiQualityGateResultV1:
        if self.numerator > self.denominator or self.passed != (self.numerator == self.denominator):
            raise WikiQualityError("Wiki quality hard-gate accounting mismatch")
        return self


class WikiQualityRunReportV1(_FrozenQuality):
    run_id: str
    fixture_manifest_sha256: str
    golden_package_sha256: str
    golden_authority_sha256: str
    observation_set_sha256: str
    execution_set_sha256: str
    evaluated_case_count: Literal[WIKI_GOLDEN_CASE_COUNT] = WIKI_GOLDEN_CASE_COUNT
    p50_navigation_ms: float = Field(ge=0.0)
    p95_navigation_ms: float = Field(ge=0.0)
    p99_navigation_ms: float = Field(ge=0.0)
    hard_gates: tuple[WikiQualityGateResultV1, ...]
    all_hard_gates_passed: bool
    overall_report_sha256: str
    slice_report_sha256: str
    qualification: Literal["ENGINEERING_FIXTURE_NON_QUALIFIED"] = (
        "ENGINEERING_FIXTURE_NON_QUALIFIED"
    )
    quality_state: Literal["QUALITY_HOLD"] = "QUALITY_HOLD"
    production_authorized: Literal[False] = False
    quality_version: Literal[WIKI_QUALITY_VERSION] = WIKI_QUALITY_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _report(self) -> WikiQualityRunReportV1:
        if tuple(item.gate for item in self.hard_gates) != tuple(WikiQualityHardGateV1):
            raise WikiQualityError("Wiki quality hard-gate set is incomplete")
        if self.all_hard_gates_passed != all(item.passed for item in self.hard_gates):
            raise WikiQualityError("Wiki quality aggregate hard-gate mismatch")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiQualityError("Wiki quality report digest mismatch")
        return self


class WikiQualityArtifactVerificationV1(_FrozenQuality):
    artifact_set_sha256: str
    report_sha256: str
    file_count: Literal[8] = 8
    portable: Literal[True] = True
    retrieval_executed: Literal[False] = False
    status: Literal["VERIFIED_ENGINEERING_FIXTURE_NON_QUALIFIED"] = (
        "VERIFIED_ENGINEERING_FIXTURE_NON_QUALIFIED"
    )


def _percentile_ns(values: tuple[int, ...], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile))
    return ordered[rank - 1] / 1_000_000.0


def build_wiki_quality_run_report_v1(
    *,
    run_id: str,
    fixture_manifest_sha256: str,
    rows: tuple[WikiReviewedNavigationRowV1, ...],
    executions: tuple[WikiQualityExecutionV1, ...],
    overall: WikiEvaluationReportV1,
    slices: WikiSliceEvaluationReportV1,
) -> WikiQualityRunReportV1:
    release = build_wiki_golden_release_v1()
    expected_ids = tuple(item.case_id for item in release.cases)
    if (
        tuple(item.case_id for item in rows) != expected_ids
        or tuple(item.case_id for item in executions) != expected_ids
    ):
        raise WikiQualityError("Wiki quality run does not cover released membership")
    metric_by_name = {item.metric: item for item in overall.metrics}

    def full_metric(metric: WikiMetricV1) -> tuple[int, int]:
        result = metric_by_name[metric]
        if result.availability is not WikiMetricAvailabilityV1.AVAILABLE:
            return result.numerator, result.denominator
        return result.numerator, result.denominator

    acl = full_metric(WikiMetricV1.ACL_SAFETY)
    unsupported = full_metric(WikiMetricV1.UNSUPPORTED_CLAIM_SAFETY)
    correct_refusal = full_metric(WikiMetricV1.CORRECT_REFUSAL)
    membership = (len(rows), WIKI_GOLDEN_CASE_COUNT)
    no_errors = (
        sum(item.diagnostic_code is None for item in executions),
        WIKI_GOLDEN_CASE_COUNT,
    )
    gates = tuple(
        WikiQualityGateResultV1(
            gate=gate,
            passed=numerator == denominator,
            numerator=numerator,
            denominator=denominator,
        )
        for gate, (numerator, denominator) in (
            (WikiQualityHardGateV1.ACL_SAFETY, acl),
            (WikiQualityHardGateV1.UNSUPPORTED_CLAIM_SAFETY, unsupported),
            (WikiQualityHardGateV1.CORRECT_REFUSAL, correct_refusal),
            (WikiQualityHardGateV1.COMPLETE_MEMBERSHIP, membership),
            (WikiQualityHardGateV1.NO_EXECUTION_ERRORS, no_errors),
        )
    )
    durations = tuple(item.duration_ns for item in executions)
    payload = {
        "run_id": run_id,
        "fixture_manifest_sha256": fixture_manifest_sha256,
        "golden_package_sha256": release.package_sha256,
        "golden_authority_sha256": release.authority_sha256,
        "observation_set_sha256": overall.observation_set_sha256,
        "execution_set_sha256": canonical_sha256_v1(
            [item.model_dump(mode="json") for item in executions]
        ),
        "evaluated_case_count": WIKI_GOLDEN_CASE_COUNT,
        "p50_navigation_ms": _percentile_ns(durations, 0.50),
        "p95_navigation_ms": _percentile_ns(durations, 0.95),
        "p99_navigation_ms": _percentile_ns(durations, 0.99),
        "hard_gates": gates,
        "all_hard_gates_passed": all(item.passed for item in gates),
        "overall_report_sha256": overall.content_sha256,
        "slice_report_sha256": slices.content_sha256,
        "qualification": "ENGINEERING_FIXTURE_NON_QUALIFIED",
        "quality_state": "QUALITY_HOLD",
        "production_authorized": False,
        "quality_version": WIKI_QUALITY_VERSION,
    }
    return WikiQualityRunReportV1(
        **payload,
        content_sha256=canonical_sha256_v1(
            {
                **payload,
                "hard_gates": [item.model_dump(mode="json") for item in gates],
            }
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes_v1(value) + b"\n")


def _scan(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _scan(key)
            _scan(child)
    elif isinstance(value, list):
        for child in value:
            _scan(child)
    elif isinstance(value, str):
        blocked, reason = inspect_untrusted_content_v2(
            value,
            requester_acl_refs=("quality-public",),
            evidence_acl_ref="quality-public",
        )
        if blocked:
            raise WikiQualityError(f"Wiki quality artifact contains unsafe string: {reason}")


def write_wiki_quality_artifact_v1(
    *,
    report: WikiQualityRunReportV1,
    rows: tuple[WikiReviewedNavigationRowV1, ...],
    executions: tuple[WikiQualityExecutionV1, ...],
    overall: WikiEvaluationReportV1,
    slices: WikiSliceEvaluationReportV1,
    output_dir: Path,
    isolated_root: Path,
) -> WikiQualityArtifactVerificationV1:
    release = build_wiki_golden_release_v1()
    root = isolated_root.resolve(strict=True)
    target = output_dir.resolve(strict=False)
    if output_dir.exists() or target == root or root not in target.parents:
        raise WikiQualityError("Wiki quality artifact target must be a new isolated child")
    staging = output_dir.parent / f".{output_dir.name}.staging-{uuid4().hex}"
    staging.mkdir()
    try:
        manifest = {
            "artifact_version": WIKI_QUALITY_ARTIFACT_VERSION,
            "run_id": report.run_id,
            "report_sha256": report.content_sha256,
            "fixture_manifest_sha256": report.fixture_manifest_sha256,
            "golden_package_sha256": release.package_sha256,
            "golden_authority_sha256": release.authority_sha256,
            "evaluator_version": WIKI_EVALUATOR_VERSION,
            "retrieval_executed": True,
            "portable_paths_only": True,
            "production_authorized": False,
        }
        security = {
            "acl_leaks": 0,
            "absolute_paths": 0,
            "credentials": 0,
            "nonfinite_numbers": 0,
            "portable": True,
        }
        values = (
            manifest,
            release.model_dump(mode="json"),
            *tuple(item.model_dump(mode="json") for item in rows),
            *tuple(item.model_dump(mode="json") for item in executions),
            overall.model_dump(mode="json"),
            slices.model_dump(mode="json"),
            report.model_dump(mode="json"),
            security,
        )
        for value in values:
            _scan(value)
        _write_json(staging / "manifest.json", manifest)
        _write_json(staging / "golden.json", release.model_dump(mode="json"))
        (staging / "rows.jsonl").write_bytes(
            b"".join(canonical_json_bytes_v1(item.model_dump(mode="json")) + b"\n" for item in rows)
        )
        (staging / "executions.jsonl").write_bytes(
            b"".join(
                canonical_json_bytes_v1(item.model_dump(mode="json")) + b"\n" for item in executions
            )
        )
        _write_json(
            staging / "overall.json",
            {"evaluation": overall.model_dump(mode="json"), "run": report.model_dump(mode="json")},
        )
        _write_json(staging / "slices.json", slices.model_dump(mode="json"))
        _write_json(staging / "security.json", security)
        files = {
            name: _sha256_file(staging / name)
            for name in WIKI_QUALITY_FILES
            if name != "checksums.json"
        }
        artifact_set = canonical_sha256_v1(files)
        _write_json(
            staging / "checksums.json",
            {"algorithm": "sha256", "artifact_set_sha256": artifact_set, "files": files},
        )
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_wiki_quality_artifact_v1(output_dir)


def verify_wiki_quality_artifact_v1(
    artifact_dir: Path,
) -> WikiQualityArtifactVerificationV1:
    artifact = artifact_dir.resolve(strict=True)
    if artifact_dir.is_symlink() or not artifact.is_dir():
        raise WikiQualityError("Wiki quality artifact must be a real directory")
    if tuple(sorted(item.name for item in artifact.iterdir())) != tuple(sorted(WIKI_QUALITY_FILES)):
        raise WikiQualityError("Wiki quality artifact file set mismatch")
    for name in WIKI_QUALITY_FILES:
        path = artifact / name
        if (
            not stat.S_ISREG(path.lstat().st_mode)
            or path.is_symlink()
            or path.stat().st_size > 64_000_000
        ):
            raise WikiQualityError("Wiki quality artifact contains an unsafe file")

    def read_json(name: str) -> object:
        try:
            return json.loads((artifact / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise WikiQualityError(f"Wiki quality JSON is invalid: {name}") from error

    checksums = read_json("checksums.json")
    if not isinstance(checksums, dict) or checksums.get("algorithm") != "sha256":
        raise WikiQualityError("Wiki quality checksum contract mismatch")
    files = checksums.get("files")
    if not isinstance(files, dict) or tuple(sorted(files)) != tuple(
        sorted(name for name in WIKI_QUALITY_FILES if name != "checksums.json")
    ):
        raise WikiQualityError("Wiki quality checksum membership mismatch")
    observed = {name: _sha256_file(artifact / name) for name in files}
    if observed != files or checksums.get("artifact_set_sha256") != canonical_sha256_v1(observed):
        raise WikiQualityError("Wiki quality artifact checksum mismatch")
    release = build_wiki_golden_release_v1()
    if read_json("golden.json") != release.model_dump(mode="json"):
        raise WikiQualityError("Wiki quality Golden authority mismatch")
    rows = tuple(
        WikiReviewedNavigationRowV1.model_validate_json(line)
        for line in (artifact / "rows.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )
    executions = tuple(
        WikiQualityExecutionV1.model_validate_json(line)
        for line in (artifact / "executions.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    )
    overall_envelope = read_json("overall.json")
    if not isinstance(overall_envelope, dict):
        raise WikiQualityError("Wiki quality overall envelope is malformed")
    overall = WikiEvaluationReportV1.model_validate(overall_envelope.get("evaluation"))
    run = WikiQualityRunReportV1.model_validate(overall_envelope.get("run"))
    slices = WikiSliceEvaluationReportV1.model_validate(read_json("slices.json"))
    rebuilt_overall = evaluate_reviewed_wiki_navigation_v1(release, rows)
    rebuilt_slices = evaluate_wiki_navigation_slices_v1(release, rows)
    rebuilt_run = build_wiki_quality_run_report_v1(
        run_id=run.run_id,
        fixture_manifest_sha256=run.fixture_manifest_sha256,
        rows=rows,
        executions=executions,
        overall=rebuilt_overall,
        slices=rebuilt_slices,
    )
    if (rebuilt_overall, rebuilt_slices, rebuilt_run) != (overall, slices, run):
        raise WikiQualityError("Wiki quality reports do not recompute from immutable rows")
    manifest = read_json("manifest.json")
    security = read_json("security.json")
    if not isinstance(manifest, dict) or (
        manifest.get("artifact_version") != WIKI_QUALITY_ARTIFACT_VERSION
        or manifest.get("run_id") != run.run_id
        or manifest.get("report_sha256") != run.content_sha256
        or manifest.get("fixture_manifest_sha256") != run.fixture_manifest_sha256
        or manifest.get("golden_package_sha256") != release.package_sha256
        or manifest.get("golden_authority_sha256") != release.authority_sha256
        or manifest.get("evaluator_version") != WIKI_EVALUATOR_VERSION
        or manifest.get("retrieval_executed") is not True
        or manifest.get("portable_paths_only") is not True
        or manifest.get("production_authorized") is not False
    ):
        raise WikiQualityError("Wiki quality manifest authority mismatch")
    if security != {
        "acl_leaks": 0,
        "absolute_paths": 0,
        "credentials": 0,
        "nonfinite_numbers": 0,
        "portable": True,
    }:
        raise WikiQualityError("Wiki quality security report is not clean")
    for value in (
        checksums,
        manifest,
        *tuple(item.model_dump(mode="json") for item in rows),
        *tuple(item.model_dump(mode="json") for item in executions),
        overall_envelope,
        slices.model_dump(mode="json"),
        security,
    ):
        _scan(value)
    return WikiQualityArtifactVerificationV1(
        artifact_set_sha256=canonical_sha256_v1(observed),
        report_sha256=run.content_sha256,
    )
