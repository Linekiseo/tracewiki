"""Measured S-layer capacity contracts and portable Wiki benchmark evidence."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import shutil
import stat
import tracemalloc
from collections.abc import Callable, Mapping
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from time import perf_counter_ns
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..global_governance_v2 import inspect_untrusted_content_v2
from .contracts_v1 import canonical_json_bytes_v1, canonical_sha256_v1

WIKI_CAPACITY_VERSION = "agent-native-wiki-s-capacity-v1"
WIKI_CAPACITY_ARTIFACT_VERSION = "wiki-capacity-portable-artifact-v1"
WIKI_CAPACITY_FILES = (
    "manifest.json",
    "samples.jsonl",
    "report.json",
    "environment.json",
    "security.json",
    "checksums.json",
)


class WikiCapacityError(ValueError):
    """Raised when benchmark measurement or portable evidence is not trustworthy."""


class WikiCapacityOperationV1(StrEnum):
    PATH_GET = "path_get"
    DIRECTORY_LIST = "directory_list"
    HYBRID_SEARCH = "hybrid_search"
    STANDARD_NAVIGATION = "standard_navigation"
    DEEP_NAVIGATION = "deep_navigation"
    COMPILE = "compile"
    PUBLISH = "publish"
    ROLLBACK = "rollback"


WIKI_CAPACITY_P95_TARGET_MS: dict[WikiCapacityOperationV1, float] = {
    WikiCapacityOperationV1.PATH_GET: 10.0,
    WikiCapacityOperationV1.DIRECTORY_LIST: 20.0,
    WikiCapacityOperationV1.HYBRID_SEARCH: 150.0,
    WikiCapacityOperationV1.STANDARD_NAVIGATION: 1_500.0,
    WikiCapacityOperationV1.DEEP_NAVIGATION: 4_000.0,
    WikiCapacityOperationV1.COMPILE: 4_000.0,
    WikiCapacityOperationV1.PUBLISH: 2_000.0,
    WikiCapacityOperationV1.ROLLBACK: 30_000.0,
}


class _FrozenCapacity(BaseModel):
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


class WikiCapacityObservationV1(_FrozenCapacity):
    result_count: int = Field(default=0, ge=0)
    work_units: int = Field(default=0, ge=0)


class WikiCapacitySampleV1(_FrozenCapacity):
    ordinal: int = Field(ge=1)
    operation: WikiCapacityOperationV1
    duration_ns: int = Field(ge=1)
    success: bool
    result_count: int = Field(ge=0)
    work_units: int = Field(ge=0)
    diagnostic_code: str | None
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiCapacitySampleV1:
        if self.success == (self.diagnostic_code is not None):
            raise WikiCapacityError("capacity sample success and diagnostic disagree")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiCapacityError("capacity sample digest mismatch")
        return self


class WikiCapacityMetricV1(_FrozenCapacity):
    operation: WikiCapacityOperationV1
    sample_count: int = Field(ge=1)
    success_count: int = Field(ge=0)
    p50_ms: float = Field(ge=0.0)
    p95_ms: float = Field(ge=0.0)
    p99_ms: float = Field(ge=0.0)
    max_ms: float = Field(ge=0.0)
    throughput_per_second: float | None = Field(default=None, ge=0.0)
    target_p95_ms: float = Field(gt=0.0)
    meets_slo: bool

    @model_validator(mode="after")
    def _metric(self) -> WikiCapacityMetricV1:
        expected = self.success_count == self.sample_count and self.p95_ms <= self.target_p95_ms
        if self.meets_slo != expected:
            raise WikiCapacityError("capacity SLO decision does not match measurements")
        return self


class WikiCapacityReportV1(_FrozenCapacity):
    benchmark_id: str
    project_id: str
    layer: Literal["S"] = "S"
    wiki_page_count: int = Field(ge=1, le=50_000)
    source_ref_count: int = Field(ge=1, le=500_000)
    database_bytes: int = Field(ge=0)
    peak_allocated_bytes: int = Field(ge=0)
    warmup_iterations: int = Field(ge=0, le=100)
    measured_iterations: int = Field(ge=1, le=10_000)
    metrics: tuple[WikiCapacityMetricV1, ...] = Field(min_length=1)
    sample_set_sha256: str
    complete_operation_set: bool
    all_slo_passed: bool
    qualification: Literal["ENGINEERING_S_CAPACITY_EVIDENCE", "SMOKE_NON_QUALIFIED"]
    production_authorized: Literal[False] = False
    benchmark_version: Literal[WIKI_CAPACITY_VERSION] = WIKI_CAPACITY_VERSION
    content_sha256: str

    @model_validator(mode="after")
    def _report(self) -> WikiCapacityReportV1:
        operation_order = {item: index for index, item in enumerate(WikiCapacityOperationV1)}
        operations = tuple(item.operation for item in self.metrics)
        if operations != tuple(sorted(set(operations), key=operation_order.__getitem__)):
            raise WikiCapacityError("capacity metric operation order is not canonical")
        complete = operations == tuple(WikiCapacityOperationV1)
        if self.complete_operation_set != complete:
            raise WikiCapacityError("capacity operation-set declaration mismatch")
        if self.all_slo_passed != all(item.meets_slo for item in self.metrics):
            raise WikiCapacityError("capacity aggregate SLO decision mismatch")
        qualified = (
            complete
            and self.all_slo_passed
            and all(item.sample_count >= 3 for item in self.metrics)
        )
        expected_qualification = (
            "ENGINEERING_S_CAPACITY_EVIDENCE" if qualified else "SMOKE_NON_QUALIFIED"
        )
        if self.qualification != expected_qualification:
            raise WikiCapacityError("capacity qualification is not evidence-derived")
        expected = canonical_sha256_v1(self.model_dump(mode="json", exclude={"content_sha256"}))
        if self.content_sha256 != expected:
            raise WikiCapacityError("capacity report digest mismatch")
        return self


class WikiCapacityArtifactVerificationV1(_FrozenCapacity):
    artifact_set_sha256: str
    report_sha256: str
    file_count: Literal[6] = 6
    portable: Literal[True] = True
    retrieval_executed: Literal[False] = False
    status: Literal["VERIFIED_ENGINEERING_CAPACITY", "VERIFIED_SMOKE_NON_QUALIFIED"]


def _percentile(values: tuple[int, ...], percentile: float) -> float:
    if not values:
        raise WikiCapacityError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1] / 1_000_000.0


def _sample(
    *,
    ordinal: int,
    operation: WikiCapacityOperationV1,
    duration_ns: int,
    success: bool,
    observation: WikiCapacityObservationV1,
    diagnostic_code: str | None,
) -> WikiCapacitySampleV1:
    payload = {
        "ordinal": ordinal,
        "operation": operation,
        "duration_ns": max(1, duration_ns),
        "success": success,
        "result_count": observation.result_count,
        "work_units": observation.work_units,
        "diagnostic_code": diagnostic_code,
    }
    return WikiCapacitySampleV1(
        **payload,
        content_sha256=canonical_sha256_v1({**payload, "operation": operation.value}),
    )


def build_wiki_capacity_report_v1(
    *,
    benchmark_id: str,
    project_id: str,
    wiki_page_count: int,
    source_ref_count: int,
    database_bytes: int,
    peak_allocated_bytes: int,
    warmup_iterations: int,
    measured_iterations: int,
    samples: tuple[WikiCapacitySampleV1, ...],
) -> WikiCapacityReportV1:
    if tuple(item.ordinal for item in samples) != tuple(range(1, len(samples) + 1)):
        raise WikiCapacityError("capacity sample ordinals are not contiguous")
    metrics: list[WikiCapacityMetricV1] = []
    for operation in WikiCapacityOperationV1:
        selected = tuple(item for item in samples if item.operation is operation)
        if not selected:
            continue
        durations = tuple(item.duration_ns for item in selected)
        total_work = sum(item.work_units for item in selected if item.success)
        total_seconds = sum(item.duration_ns for item in selected if item.success) / 1_000_000_000
        p95 = _percentile(durations, 0.95)
        metrics.append(
            WikiCapacityMetricV1(
                operation=operation,
                sample_count=len(selected),
                success_count=sum(item.success for item in selected),
                p50_ms=_percentile(durations, 0.50),
                p95_ms=p95,
                p99_ms=_percentile(durations, 0.99),
                max_ms=max(durations) / 1_000_000.0,
                throughput_per_second=(
                    total_work / total_seconds if total_work and total_seconds else None
                ),
                target_p95_ms=WIKI_CAPACITY_P95_TARGET_MS[operation],
                meets_slo=all(item.success for item in selected)
                and p95 <= WIKI_CAPACITY_P95_TARGET_MS[operation],
            )
        )
    complete = tuple(item.operation for item in metrics) == tuple(WikiCapacityOperationV1)
    all_passed = all(item.meets_slo for item in metrics)
    sufficiently_sampled = all(item.sample_count >= 3 for item in metrics)
    payload = {
        "benchmark_id": benchmark_id,
        "project_id": project_id,
        "layer": "S",
        "wiki_page_count": wiki_page_count,
        "source_ref_count": source_ref_count,
        "database_bytes": database_bytes,
        "peak_allocated_bytes": peak_allocated_bytes,
        "warmup_iterations": warmup_iterations,
        "measured_iterations": measured_iterations,
        "metrics": tuple(metrics),
        "sample_set_sha256": canonical_sha256_v1(
            [item.model_dump(mode="json") for item in samples]
        ),
        "complete_operation_set": complete,
        "all_slo_passed": all_passed,
        "qualification": (
            "ENGINEERING_S_CAPACITY_EVIDENCE"
            if complete and all_passed and sufficiently_sampled
            else "SMOKE_NON_QUALIFIED"
        ),
        "production_authorized": False,
        "benchmark_version": WIKI_CAPACITY_VERSION,
    }
    return WikiCapacityReportV1(
        **payload,
        content_sha256=canonical_sha256_v1(
            {
                **payload,
                "metrics": [item.model_dump(mode="json") for item in metrics],
            }
        ),
    )


def measure_wiki_capacity_v1(
    *,
    benchmark_id: str,
    project_id: str,
    operations: Mapping[WikiCapacityOperationV1, Callable[[], WikiCapacityObservationV1]],
    wiki_page_count: int,
    source_ref_count: int,
    database_bytes: int,
    warmup_iterations: int = 2,
    measured_iterations: int = 20,
    clock_ns: Callable[[], int] = perf_counter_ns,
) -> tuple[WikiCapacityReportV1, tuple[WikiCapacitySampleV1, ...]]:
    if not operations:
        raise WikiCapacityError("capacity benchmark has no operations")
    if tuple(operations) != tuple(
        operation for operation in WikiCapacityOperationV1 if operation in operations
    ):
        raise WikiCapacityError("capacity operation mapping order is not canonical")
    if not 0 <= warmup_iterations <= 100 or not 1 <= measured_iterations <= 10_000:
        raise WikiCapacityError("capacity benchmark iteration count is outside bounds")
    for _ in range(warmup_iterations):
        for callback in operations.values():
            callback()

    samples: list[WikiCapacitySampleV1] = []
    ordinal = 0
    for _ in range(measured_iterations):
        for operation, callback in operations.items():
            ordinal += 1
            started = clock_ns()
            try:
                observation = callback()
            except Exception as error:
                duration = max(1, clock_ns() - started)
                samples.append(
                    _sample(
                        ordinal=ordinal,
                        operation=operation,
                        duration_ns=duration,
                        success=False,
                        observation=WikiCapacityObservationV1(),
                        diagnostic_code=type(error).__name__,
                    )
                )
            else:
                duration = max(1, clock_ns() - started)
                samples.append(
                    _sample(
                        ordinal=ordinal,
                        operation=operation,
                        duration_ns=duration,
                        success=True,
                        observation=observation,
                        diagnostic_code=None,
                    )
                )
    # Allocation profiling is a separate pass.  Keeping tracemalloc outside the
    # latency loop avoids reporting profiler overhead as product latency while still
    # recording a real peak for the same canonical operation set.
    tracing_before = tracemalloc.is_tracing()
    if not tracing_before:
        tracemalloc.start()
    tracemalloc.reset_peak()
    for callback in operations.values():
        with suppress(Exception):
            callback()
    _, peak = tracemalloc.get_traced_memory()
    if not tracing_before:
        tracemalloc.stop()
    sample_tuple = tuple(samples)
    return (
        build_wiki_capacity_report_v1(
            benchmark_id=benchmark_id,
            project_id=project_id,
            wiki_page_count=wiki_page_count,
            source_ref_count=source_ref_count,
            database_bytes=database_bytes,
            peak_allocated_bytes=peak,
            warmup_iterations=warmup_iterations,
            measured_iterations=measured_iterations,
            samples=sample_tuple,
        ),
        sample_tuple,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_bytes(canonical_json_bytes_v1(value) + b"\n")


def _scan_portable_strings(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            _scan_portable_strings(key)
            _scan_portable_strings(child)
    elif isinstance(value, list):
        for child in value:
            _scan_portable_strings(child)
    elif isinstance(value, str):
        blocked, reason = inspect_untrusted_content_v2(
            value,
            requester_acl_refs=("capacity-public",),
            evidence_acl_ref="capacity-public",
        )
        if blocked:
            raise WikiCapacityError(f"capacity artifact contains unsafe string: {reason}")


def write_wiki_capacity_artifact_v1(
    *,
    report: WikiCapacityReportV1,
    samples: tuple[WikiCapacitySampleV1, ...],
    output_dir: Path,
    isolated_root: Path,
) -> WikiCapacityArtifactVerificationV1:
    root = isolated_root.resolve(strict=True)
    target = output_dir.resolve(strict=False)
    if target == root or root not in target.parents or output_dir.exists():
        raise WikiCapacityError("capacity artifact target must be a new isolated child")
    staging = output_dir.parent / f".{output_dir.name}.staging-{uuid4().hex}"
    if staging.exists() or staging.is_symlink():
        raise WikiCapacityError("capacity artifact staging path already exists")
    staging.mkdir(parents=False)
    try:
        manifest = {
            "artifact_version": WIKI_CAPACITY_ARTIFACT_VERSION,
            "benchmark_id": report.benchmark_id,
            "report_sha256": report.content_sha256,
            "sample_set_sha256": report.sample_set_sha256,
            "portable_paths_only": True,
            "production_authorized": False,
        }
        environment = {
            "implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        }
        security = {
            "absolute_paths": 0,
            "credentials": 0,
            "database_files": 0,
            "nonfinite_numbers": 0,
            "portable": True,
        }
        for value in (manifest, report.model_dump(mode="json"), environment, security):
            _scan_portable_strings(value)
        _write_json(staging / "manifest.json", manifest)
        (staging / "samples.jsonl").write_bytes(
            b"".join(
                canonical_json_bytes_v1(item.model_dump(mode="json")) + b"\n" for item in samples
            )
        )
        _write_json(staging / "report.json", report.model_dump(mode="json"))
        _write_json(staging / "environment.json", environment)
        _write_json(staging / "security.json", security)
        file_hashes = {
            name: _sha256_file(staging / name)
            for name in WIKI_CAPACITY_FILES
            if name != "checksums.json"
        }
        artifact_set = canonical_sha256_v1(file_hashes)
        _write_json(
            staging / "checksums.json",
            {
                "algorithm": "sha256",
                "artifact_set_sha256": artifact_set,
                "files": file_hashes,
            },
        )
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return verify_wiki_capacity_artifact_v1(output_dir)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WikiCapacityError(f"capacity artifact JSON is invalid: {path.name}") from error


def verify_wiki_capacity_artifact_v1(
    artifact_dir: Path,
) -> WikiCapacityArtifactVerificationV1:
    artifact = artifact_dir.resolve(strict=True)
    if artifact_dir.is_symlink() or not artifact.is_dir():
        raise WikiCapacityError("capacity artifact must be a real directory")
    names = tuple(sorted(item.name for item in artifact.iterdir()))
    if names != tuple(sorted(WIKI_CAPACITY_FILES)):
        raise WikiCapacityError("capacity artifact canonical file set mismatch")
    for name in WIKI_CAPACITY_FILES:
        path = artifact / name
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.is_symlink() or path.stat().st_size > 32_000_000:
            raise WikiCapacityError("capacity artifact contains an unsafe file")
    checksums = _read_json(artifact / "checksums.json")
    if not isinstance(checksums, dict) or checksums.get("algorithm") != "sha256":
        raise WikiCapacityError("capacity checksum contract mismatch")
    expected_files = checksums.get("files")
    if not isinstance(expected_files, dict) or tuple(sorted(expected_files)) != tuple(
        sorted(name for name in WIKI_CAPACITY_FILES if name != "checksums.json")
    ):
        raise WikiCapacityError("capacity checksums do not cover the canonical files")
    observed_files = {name: _sha256_file(artifact / name) for name in expected_files}
    if observed_files != expected_files:
        raise WikiCapacityError("capacity artifact file checksum mismatch")
    artifact_set = canonical_sha256_v1(observed_files)
    if checksums.get("artifact_set_sha256") != artifact_set:
        raise WikiCapacityError("capacity artifact set checksum mismatch")
    manifest = _read_json(artifact / "manifest.json")
    report_payload = _read_json(artifact / "report.json")
    environment = _read_json(artifact / "environment.json")
    security = _read_json(artifact / "security.json")
    if not all(
        isinstance(item, dict) for item in (manifest, report_payload, environment, security)
    ):
        raise WikiCapacityError("capacity artifact objects are malformed")
    samples: list[WikiCapacitySampleV1] = []
    for line in (artifact / "samples.jsonl").read_text(encoding="utf-8").splitlines():
        if not line:
            raise WikiCapacityError("capacity sample stream contains an empty row")
        samples.append(WikiCapacitySampleV1.model_validate_json(line))
    report = WikiCapacityReportV1.model_validate(report_payload)
    rebuilt = build_wiki_capacity_report_v1(
        benchmark_id=report.benchmark_id,
        project_id=report.project_id,
        wiki_page_count=report.wiki_page_count,
        source_ref_count=report.source_ref_count,
        database_bytes=report.database_bytes,
        peak_allocated_bytes=report.peak_allocated_bytes,
        warmup_iterations=report.warmup_iterations,
        measured_iterations=report.measured_iterations,
        samples=tuple(samples),
    )
    if rebuilt != report:
        raise WikiCapacityError("capacity report does not recompute from immutable samples")
    if (
        manifest.get("artifact_version") != WIKI_CAPACITY_ARTIFACT_VERSION
        or manifest.get("benchmark_id") != report.benchmark_id
        or manifest.get("report_sha256") != report.content_sha256
        or manifest.get("sample_set_sha256") != report.sample_set_sha256
        or manifest.get("portable_paths_only") is not True
        or manifest.get("production_authorized") is not False
    ):
        raise WikiCapacityError("capacity manifest authority mismatch")
    if security != {
        "absolute_paths": 0,
        "credentials": 0,
        "database_files": 0,
        "nonfinite_numbers": 0,
        "portable": True,
    }:
        raise WikiCapacityError("capacity security report is not clean")
    for value in (checksums, manifest, report_payload, environment, security):
        _scan_portable_strings(value)
    return WikiCapacityArtifactVerificationV1(
        artifact_set_sha256=artifact_set,
        report_sha256=report.content_sha256,
        status=(
            "VERIFIED_ENGINEERING_CAPACITY"
            if report.qualification == "ENGINEERING_S_CAPACITY_EVIDENCE"
            else "VERIFIED_SMOKE_NON_QUALIFIED"
        ),
    )
