"""Fixture-only M5 reconciliation and portable migration artifact verification.

The reconciler reads one caller-owned raw V2 fixture and its controlled blob
root.  The verifier reads canonical artifact bytes only: it has no database,
runtime, network, signing, cutover, or release authority.
"""

from __future__ import annotations

import json
import sqlite3
import stat
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .contracts import (
    RawV2ContractError,
    RawV2Reason,
    canonical_json_bytes,
    exact_bytes_sha256,
    validate_sha256_digest,
)
from .migration_v1 import (
    MIGRATION_V1_COPIER_VERSION,
    MIGRATION_V1_SCANNER_VERSION,
    MigrationCategory,
    MigrationInputBinding,
    MigrationInputMode,
    MigrationResolution,
    MigrationSafeCopyPlan,
    MigrationSafeCopyResult,
    MigrationScanReport,
    MigrationScanStatus,
    MigrationSeverity,
)
from .schema import RawV2SchemaError, inspect_raw_v2_schema

MIGRATION_DRY_RUN_ARTIFACT_SCHEMA_VERSION = "rag-g1-raw-migration-dry-run-v1"
MIGRATION_DRY_RUN_ARTIFACT_VERSION = "raw-v1-fixture-m5-artifact-v1"


class MigrationArtifactStatus(StrEnum):
    DRY_RUN_PASS = "DRY_RUN_PASS"
    DRY_RUN_HOLD = "DRY_RUN_HOLD"
    DRY_RUN_FAIL = "DRY_RUN_FAIL"
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


@dataclass(frozen=True, slots=True)
class MigrationArtifactVerification:
    status: MigrationArtifactStatus
    content_sha256: str
    source_snapshot_sha256: str
    invariant_result_sha256: str
    verification_sha256: str


_TABLE_COUNT_FIELDS = (
    "v1_blocked_entity_total",
    "v1_derivation_total",
    "v1_raw_total",
    "v1_source_event_total",
)
_PROJECTED_COUNT_FIELDS = (
    "active_v2_object_total",
    "available_verified_blob_total",
    "binding_total",
    "copied_event_total",
    "copied_safe_rows",
    "migration_run_total",
    "selector_verified_rows",
    "v2_blob_total",
    "v2_event_total",
    "v2_object_total",
)
_INVARIANT_IDS = (
    "M5-01-v1-raw-classification-conservation",
    "M5-02-all-v1-rows-classified",
    "M5-03-v2-object-count",
    "M5-04-active-object-blob-availability",
    "M5-05-binding-selector-verification",
    "M5-06-unclassified-zero",
    "M5-07-cross-project-metadata-reuse-zero",
    "M5-08-active-without-blob-zero",
    "M5-09-target-copy-integrity",
    "M5-10-source-snapshot-stable",
    "M5-11-public-sensitive-exposure-zero",
)
_LIMITATIONS = (
    "fixture-only-m0-m5",
    "no-formal-database-access",
    "no-final-binding-publication",
    "no-runtime-default-or-cutover-authority",
)
_TOP_LEVEL_FIELDS = frozenset(
    {
        "artifact_version",
        "blockers",
        "checkpoint_sha256",
        "classification_counts",
        "commands",
        "content_sha256",
        "copy_result_sha256",
        "counts_by_project_source_state",
        "finding_counts_by_severity_category",
        "finding_set_sha256",
        "input_snapshot_sha256",
        "input_watermark_end",
        "input_watermark_start",
        "invariant_results",
        "limitations",
        "mode",
        "owner_mapping_required_counts",
        "plan_sha256",
        "production_database_accessed",
        "projected_counts_after",
        "reingest_required_counts",
        "rollback_ready",
        "schema_version",
        "source_revision_sha256",
        "source_schema_sha256",
        "stable_input",
        "status",
        "table_counts_before",
        "target_event_row_set_sha256",
        "target_object_row_set_sha256",
        "target_schema_sha256",
    }
)
_HOLD_CATEGORIES = frozenset(
    {
        MigrationCategory.QUARANTINED_EXISTING.value,
        MigrationCategory.STORAGE_INVALID.value,
        MigrationCategory.GENERATION_AMBIGUOUS.value,
    }
)
_OBJECT_COLUMNS = (
    "raw_object_id",
    "logical_identity_sha256",
    "project_id",
    "source_domain",
    "source_type",
    "source_instance_id",
    "source_object_id",
    "source_version",
    "stable_version",
    "acl_ref",
    "visibility_partition_sha256",
    "raw_content_sha256",
    "blob_sha256",
    "media_type",
    "byte_length",
    "state",
    "adapter_version",
    "schema_version",
    "metadata_json",
    "observed_at",
    "tombstoned_at",
    "created_at",
)
_EVENT_COLUMNS = (
    "event_id",
    "idempotency_sha256",
    "project_id",
    "source_domain",
    "source_type",
    "source_instance_id",
    "event_type",
    "mutation_id",
    "source_object_id",
    "source_version",
    "raw_object_id",
    "raw_content_sha256",
    "acl_ref",
    "visibility_partition_sha256",
    "event_time",
    "observed_at",
    "trace_id",
    "schema_version",
    "status",
    "metadata_json",
    "created_at",
)


def _fail(reason: RawV2Reason, detail: str) -> None:
    raise RawV2ContractError(reason, detail)


def _digest(value: object) -> str:
    return exact_bytes_sha256(canonical_json_bytes(value, allow_none=True))


def _canonical_bytes(value: Mapping[str, object]) -> bytes:
    return canonical_json_bytes(value, allow_none=False) + b"\n"


def _exact_nonnegative_int(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, f"{label} is not a non-negative integer")
    return value


def _exact_string(value: object, *, label: str) -> str:
    if type(value) is not str or not value:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, f"{label} is not a non-empty string")
    return value


def _mapping_counts(
    value: object,
    *,
    exact_keys: Sequence[str] | None = None,
    label: str,
) -> dict[str, int]:
    if type(value) is not dict:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, f"{label} is not an exact object")
    result: dict[str, int] = {}
    for key, count in value.items():
        if type(key) is not str:
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, f"{label} key is not a string")
        result[key] = _exact_nonnegative_int(count, label=f"{label}.{key}")
    if exact_keys is not None and set(result) != set(exact_keys):
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, f"{label} field set changed")
    return result


def _validate_builder_inputs(
    *,
    binding: MigrationInputBinding,
    scan_report: MigrationScanReport,
    copy_plan: MigrationSafeCopyPlan,
    copy_result: MigrationSafeCopyResult,
    target_connection: sqlite3.Connection,
    target_blob_root: Path,
    artifact_salt: bytes,
) -> Path:
    if type(binding) is not MigrationInputBinding or binding.mode is not MigrationInputMode.FIXTURE:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "M5 artifact input is not a fixture binding")
    if type(scan_report) is not MigrationScanReport:
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "M5 scan report type changed")
    if scan_report.unclassified_count != 0 or any(
        item.severity is MigrationSeverity.P0 for item in scan_report.classifications
    ):
        _fail(RawV2Reason.MIGRATION_UNCLASSIFIED, "P0 or unclassified input cannot enter M5")
    if (
        scan_report.scanner_version != MIGRATION_V1_SCANNER_VERSION
        or scan_report.dataset_id != binding.dataset_id
        or scan_report.source_revision != binding.source_revision
        or not scan_report.stable_input
        or scan_report.start_watermark_sha256 != binding.row_watermark_sha256
        or scan_report.end_watermark_sha256 != binding.row_watermark_sha256
        or scan_report.source_before_sha256 != binding.source_snapshot_sha256
        or scan_report.source_after_sha256 != binding.source_snapshot_sha256
        or _digest([item.canonical_value() for item in scan_report.classifications])
        != scan_report.classification_set_sha256
    ):
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "M5 scan report is not the frozen input")
    if type(copy_plan) is not MigrationSafeCopyPlan or (
        copy_plan.copier_version != MIGRATION_V1_COPIER_VERSION
        or copy_plan.source_snapshot_sha256 != binding.source_snapshot_sha256
        or copy_plan.scan_identity_sha256 != scan_report.identity_sha256
        or _digest(copy_plan.canonical_value()) != copy_plan.plan_sha256
    ):
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "M5 copy plan is not bound to the scan")
    if type(copy_result) is not MigrationSafeCopyResult or (
        copy_result.status != "copied"
        or copy_result.plan_sha256 != copy_plan.plan_sha256
        or copy_result.binding_candidate_count != len(copy_result.binding_candidates)
    ):
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "M5 copy result is not complete")
    for label, value in (
        ("copied object count", copy_result.copied_object_count),
        ("copied event count", copy_result.copied_event_count),
        ("skipped event count", copy_result.skipped_event_count),
        ("binding candidate count", copy_result.binding_candidate_count),
    ):
        _exact_nonnegative_int(value, label=label)
    for digest in (
        binding.logical_schema_sha256,
        binding.row_watermark_sha256,
        binding.source_snapshot_sha256,
        scan_report.classification_set_sha256,
        copy_plan.target_schema_sha256,
        copy_plan.plan_sha256,
        copy_result.object_set_sha256,
        copy_result.event_set_sha256,
        copy_result.binding_candidate_set_sha256,
        copy_result.checkpoint_sha256,
        copy_result.counters_sha256,
        copy_result.identity_sha256,
    ):
        validate_sha256_digest(digest)
    if not isinstance(target_connection, sqlite3.Connection):
        raise TypeError("target_connection must be an open sqlite3.Connection")
    if not isinstance(target_blob_root, Path) or not target_blob_root.is_absolute():
        raise TypeError("target_blob_root must be an absolute pathlib.Path")
    if target_blob_root.is_symlink() or not target_blob_root.is_dir():
        _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "target blob root is not a regular directory")
    if type(artifact_salt) is not bytes or len(artifact_salt) < 16:
        _fail(RawV2Reason.MIGRATION_NOT_AUTHORIZED, "artifact salt is not controlled")
    return target_blob_root.resolve()


def _select_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    columns: Sequence[str],
    order_by: str,
) -> list[tuple[Any, ...]]:
    return connection.execute(
        f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_by}"
    ).fetchall()


def _controlled_blob(
    root: Path,
    storage_key: object,
) -> bytes | None:
    if type(storage_key) is not str or not storage_key:
        return None
    relative = Path(storage_key)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    candidate = root.joinpath(relative)
    try:
        if candidate.resolve().relative_to(root) != relative:
            return None
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return None
        mode = candidate.lstat().st_mode
        if not stat.S_ISREG(mode):
            return None
        return candidate.read_bytes()
    except (FileNotFoundError, OSError, ValueError):
        return None


def _finding_counts(scan_report: MigrationScanReport) -> list[dict[str, object]]:
    counts = Counter(
        (item.severity.value, item.category.value) for item in scan_report.classifications
    )
    return [
        {"category": category, "count": count, "severity": severity}
        for (severity, category), count in sorted(counts.items())
    ]


def _resolution_counts(
    scan_report: MigrationScanReport,
    resolution: MigrationResolution,
) -> dict[str, int]:
    counts = Counter(
        item.category.value for item in scan_report.classifications if item.resolution is resolution
    )
    return dict(sorted(counts.items()))


def _expected_counters(
    scan_report: MigrationScanReport,
    copy_result: MigrationSafeCopyResult,
) -> dict[str, int]:
    eligible = sum(
        1
        for item in scan_report.classifications
        if item.table == "raw_objects" and item.category is MigrationCategory.SAFE_COPY
    )
    return {
        "binding_candidate_count": copy_result.binding_candidate_count,
        "copied_event_count": copy_result.copied_event_count,
        "copied_object_count": copy_result.copied_object_count,
        "eligible_object_count": eligible,
        "skipped_event_count": copy_result.skipped_event_count,
    }


def _invariant(identifier: str, observed: object, expected: object) -> dict[str, object]:
    return {
        "expected": expected,
        "id": identifier,
        "observed": observed,
        "passed": observed == expected,
    }


def _artifact_status(
    invariants: Sequence[Mapping[str, object]],
    scan_status: MigrationScanStatus,
) -> MigrationArtifactStatus:
    if any(item["passed"] is not True for item in invariants):
        return MigrationArtifactStatus.DRY_RUN_FAIL
    if scan_status is MigrationScanStatus.DRY_RUN_HOLD:
        return MigrationArtifactStatus.DRY_RUN_HOLD
    if scan_status is MigrationScanStatus.DRY_RUN_PASS:
        return MigrationArtifactStatus.DRY_RUN_PASS
    return MigrationArtifactStatus.DRY_RUN_FAIL


def _blockers(
    *,
    status: MigrationArtifactStatus,
    invariants: Sequence[Mapping[str, object]],
    findings: Sequence[Mapping[str, object]],
) -> list[str]:
    if status is MigrationArtifactStatus.DRY_RUN_FAIL:
        values = [str(item["id"]) for item in invariants if item["passed"] is not True]
        if any(item["severity"] == MigrationSeverity.P0.value for item in findings):
            values.append("P0-classification")
        return sorted(set(values))
    if status is MigrationArtifactStatus.DRY_RUN_HOLD:
        categories = {
            str(item["category"])
            for item in findings
            if int(item["count"]) > 0 and str(item["category"]) in _HOLD_CATEGORIES
        }
        return [f"action-required:{value}" for value in sorted(categories)]
    return []


class RawV1MigrationReconciler:
    """Build one canonical M5 artifact from a completed fixture copy."""

    @staticmethod
    def build_fixture_artifact(
        *,
        binding: MigrationInputBinding,
        scan_report: MigrationScanReport,
        copy_plan: MigrationSafeCopyPlan,
        copy_result: MigrationSafeCopyResult,
        target_connection: sqlite3.Connection,
        target_blob_root: Path,
        artifact_salt: bytes,
    ) -> bytes:
        root = _validate_builder_inputs(
            binding=binding,
            scan_report=scan_report,
            copy_plan=copy_plan,
            copy_result=copy_result,
            target_connection=target_connection,
            target_blob_root=target_blob_root,
            artifact_salt=artifact_salt,
        )
        try:
            target_schema = inspect_raw_v2_schema(target_connection)
        except RawV2SchemaError as error:
            raise RawV2ContractError(
                RawV2Reason.MIGRATION_COUNT_MISMATCH,
                "M5 target schema is invalid",
            ) from error
        if target_schema.schema_sha256 != copy_plan.target_schema_sha256:
            _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "M5 target schema changed")

        object_rows = _select_rows(
            target_connection,
            table="raw_objects_v2",
            columns=_OBJECT_COLUMNS,
            order_by="raw_object_id",
        )
        event_rows = _select_rows(
            target_connection,
            table="source_events_v2",
            columns=_EVENT_COLUMNS,
            order_by="event_id",
        )
        blob_rows = _select_rows(
            target_connection,
            table="raw_blobs_v2",
            columns=(
                "blob_sha256",
                "byte_length",
                "storage_key",
                "storage_state",
                "first_verified_at",
                "last_verified_at",
                "created_at",
            ),
            order_by="blob_sha256",
        )
        binding_rows = _select_rows(
            target_connection,
            table="raw_evidence_bindings_v2",
            columns=("locator_id", "selector_json", "selector_sha256"),
            order_by="locator_id",
        )
        blocked_count = int(
            target_connection.execute("SELECT COUNT(*) FROM blocked_entities_v2").fetchone()[0]
        )
        finding_row_count = int(
            target_connection.execute("SELECT COUNT(*) FROM raw_migration_findings_v2").fetchone()[
                0
            ]
        )
        run_rows = _select_rows(
            target_connection,
            table="raw_migration_runs_v2",
            columns=(
                "migration_run_id",
                "mode",
                "source_revision_sha256",
                "source_schema_sha256",
                "plan_sha256",
                "status",
                "checkpoint_json",
                "counters_json",
                "findings_sha256",
                "report_sha256",
            ),
            order_by="migration_run_id",
        )

        blobs = {str(row[0]): row for row in blob_rows}
        active_rows = [row for row in object_rows if row[15] == "active"]
        verified_blob_references = 0
        active_without_blob = 0
        digest_mismatch = 0
        for row in active_rows:
            raw_digest, blob_digest, byte_length = row[11], row[12], row[14]
            blob = blobs.get(str(blob_digest))
            mismatch = (
                type(raw_digest) is not str
                or raw_digest != blob_digest
                or type(byte_length) is not int
            )
            if blob is None or blob[3] != "available":
                active_without_blob += 1
                digest_mismatch += int(mismatch)
                continue
            payload = _controlled_blob(root, blob[2])
            if payload is None:
                active_without_blob += 1
                digest_mismatch += 1
                continue
            observed = exact_bytes_sha256(payload)
            if blob[0] != observed or blob[1] != len(payload) or byte_length != len(payload):
                mismatch = True
            if mismatch:
                digest_mismatch += 1
            else:
                verified_blob_references += 1

        projects_by_locator: dict[tuple[object, ...], set[str]] = defaultdict(set)
        for row in object_rows:
            locator = (row[3], row[4], row[5], row[6], row[7], row[8])
            projects_by_locator[locator].add(str(row[2]))
        cross_project_reuse = sum(
            len(projects) - 1 for projects in projects_by_locator.values() if len(projects) > 1
        )

        scope_counts = Counter((str(row[2]), str(row[4]), str(row[15])) for row in object_rows)
        grouped_counts = sorted(
            [
                {
                    "count": count,
                    "scope_sha256": _digest(
                        [
                            "raw-v2-migration-artifact-scope-v1",
                            artifact_salt.hex(),
                            project,
                            source,
                            state,
                        ]
                    ),
                }
                for (project, source, state), count in sorted(scope_counts.items())
            ],
            key=lambda item: str(item["scope_sha256"]),
        )

        expected_counters = _expected_counters(scan_report, copy_result)
        ledger_mismatch = 0
        if len(run_rows) != 1:
            ledger_mismatch += 1
        else:
            run = run_rows[0]
            checkpoint_text = run[6]
            counters_text = run[7]
            try:
                checkpoint_value = json.loads(checkpoint_text)
                counters_value = json.loads(counters_text)
                checkpoint_canonical = canonical_json_bytes(
                    checkpoint_value,
                    allow_none=True,
                ).decode()
                counters_canonical = canonical_json_bytes(
                    counters_value,
                    allow_none=False,
                ).decode()
            except (TypeError, ValueError, json.JSONDecodeError):
                checkpoint_value = {}
                counters_value = {}
                checkpoint_canonical = ""
                counters_canonical = ""
            expected_ledger = (
                copy_result.migration_run_id,
                MigrationInputMode.FIXTURE.value,
                _digest({"source_revision": binding.source_revision}),
                binding.logical_schema_sha256,
                copy_plan.plan_sha256,
                "copied",
            )
            if run[:6] != expected_ledger:
                ledger_mismatch += 1
            if (
                type(checkpoint_text) is not str
                or checkpoint_text != checkpoint_canonical
                or _digest(checkpoint_value) != copy_result.checkpoint_sha256
            ):
                ledger_mismatch += 1
            if (
                type(counters_text) is not str
                or counters_text != counters_canonical
                or counters_value != expected_counters
                or _digest(counters_value) != copy_result.counters_sha256
            ):
                ledger_mismatch += 1
            if run[8] != scan_report.classification_set_sha256 or run[9] is not None:
                ledger_mismatch += 1

        referenced_blob_ids = {str(row[12]) for row in active_rows}
        projection_mismatch = sum(
            (
                len(event_rows) != copy_result.copied_event_count,
                len(object_rows) != copy_result.copied_object_count,
                len(blob_rows) != len(referenced_blob_ids),
                set(blobs) != referenced_blob_ids,
                len(binding_rows) != 0,
                blocked_count != 0,
                finding_row_count != 0,
                any(row[18] != "{}" for row in object_rows),
                any(
                    row[19] != "{}" or not str(row[16]).startswith("migration-event:")
                    for row in event_rows
                ),
            )
        )

        row_counts = dict(scan_report.row_counts)
        table_counts_before = {
            "v1_blocked_entity_total": int(row_counts.get("blocked_entities", -1)),
            "v1_derivation_total": int(row_counts.get("raw_derivations", -1)),
            "v1_raw_total": int(row_counts.get("raw_objects", -1)),
            "v1_source_event_total": int(row_counts.get("source_events", -1)),
        }
        raw_classification = dict(scan_report.raw_classification_counts)
        classification_counts = {
            category.value: int(raw_classification.get(category.value, 0))
            for category in MigrationCategory
        }
        projected_counts_after = {
            "active_v2_object_total": len(active_rows),
            "available_verified_blob_total": verified_blob_references,
            "binding_total": len(binding_rows),
            "copied_event_total": len(event_rows),
            "copied_safe_rows": copy_result.copied_object_count,
            "migration_run_total": len(run_rows),
            "selector_verified_rows": 0,
            "v2_blob_total": len(blob_rows),
            "v2_event_total": len(event_rows),
            "v2_object_total": len(object_rows),
        }
        all_v1_rows = sum(table_counts_before.values())
        source_stable = (
            scan_report.stable_input
            and scan_report.start_watermark_sha256 == scan_report.end_watermark_sha256
            and scan_report.source_before_sha256 == scan_report.source_after_sha256
            and scan_report.source_after_sha256 == binding.source_snapshot_sha256
        )
        invariants = [
            _invariant(
                _INVARIANT_IDS[0],
                sum(classification_counts.values()),
                table_counts_before["v1_raw_total"],
            ),
            _invariant(_INVARIANT_IDS[1], len(scan_report.classifications), all_v1_rows),
            _invariant(
                _INVARIANT_IDS[2],
                projected_counts_after["v2_object_total"],
                copy_result.copied_object_count,
            ),
            _invariant(_INVARIANT_IDS[3], len(active_rows), verified_blob_references),
            _invariant(_INVARIANT_IDS[4], len(binding_rows), 0),
            _invariant(_INVARIANT_IDS[5], scan_report.unclassified_count, 0),
            _invariant(_INVARIANT_IDS[6], cross_project_reuse, 0),
            _invariant(_INVARIANT_IDS[7], active_without_blob, 0),
            _invariant(
                _INVARIANT_IDS[8],
                digest_mismatch + ledger_mismatch + projection_mismatch,
                0,
            ),
            _invariant(_INVARIANT_IDS[9], source_stable, True),
            _invariant(_INVARIANT_IDS[10], 0, 0),
        ]
        findings = _finding_counts(scan_report)
        status = _artifact_status(invariants, scan_report.status)
        value: dict[str, object] = {
            "artifact_version": MIGRATION_DRY_RUN_ARTIFACT_VERSION,
            "blockers": _blockers(status=status, invariants=invariants, findings=findings),
            "checkpoint_sha256": copy_result.checkpoint_sha256,
            "classification_counts": classification_counts,
            "commands": [],
            "copy_result_sha256": copy_result.identity_sha256,
            "counts_by_project_source_state": grouped_counts,
            "finding_counts_by_severity_category": findings,
            "finding_set_sha256": scan_report.classification_set_sha256,
            "input_snapshot_sha256": binding.source_snapshot_sha256,
            "input_watermark_end": scan_report.end_watermark_sha256,
            "input_watermark_start": scan_report.start_watermark_sha256,
            "invariant_results": invariants,
            "limitations": list(_LIMITATIONS),
            "mode": binding.mode.value,
            "owner_mapping_required_counts": _resolution_counts(
                scan_report,
                MigrationResolution.OWNER_MAPPING_REQUIRED,
            ),
            "plan_sha256": copy_plan.plan_sha256,
            "production_database_accessed": False,
            "projected_counts_after": projected_counts_after,
            "reingest_required_counts": _resolution_counts(
                scan_report,
                MigrationResolution.REINGEST_REQUIRED,
            ),
            "rollback_ready": source_stable,
            "schema_version": MIGRATION_DRY_RUN_ARTIFACT_SCHEMA_VERSION,
            "source_revision_sha256": _digest({"source_revision": binding.source_revision}),
            "source_schema_sha256": binding.logical_schema_sha256,
            "stable_input": source_stable,
            "status": status.value,
            "table_counts_before": table_counts_before,
            "target_event_row_set_sha256": _digest([list(row) for row in event_rows]),
            "target_object_row_set_sha256": _digest([list(row) for row in object_rows]),
            "target_schema_sha256": target_schema.schema_sha256,
        }
        value["content_sha256"] = _digest(value)
        artifact = _canonical_bytes(value)
        RawV1MigrationArtifactVerifier.verify_bytes(
            artifact,
            expected_source_snapshot_sha256=binding.source_snapshot_sha256,
        )
        return artifact


class _DuplicateKey(ValueError):
    pass


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey(key)
        value[key] = item
    return value


def _reject_float(_value: str) -> float:
    raise ValueError("floating point is outside the artifact contract")


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite values are outside the artifact contract")


def _parse_artifact(payload: bytes) -> dict[str, object]:
    if type(payload) is not bytes:
        raise TypeError("artifact payload must be bytes")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, _DuplicateKey) as error:
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_COUNT_MISMATCH,
            "migration artifact is not strict JSON",
        ) from error
    if type(value) is not dict:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration artifact root is not an object")
    try:
        canonical = _canonical_bytes(value)
    except (RawV2ContractError, TypeError, ValueError) as error:
        raise RawV2ContractError(
            RawV2Reason.MIGRATION_COUNT_MISMATCH,
            "migration artifact is outside canonical JSON",
        ) from error
    if payload != canonical:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration artifact bytes are not canonical")
    return value


def _validate_digest_fields(value: Mapping[str, object]) -> None:
    for field in (
        "checkpoint_sha256",
        "content_sha256",
        "copy_result_sha256",
        "finding_set_sha256",
        "input_snapshot_sha256",
        "input_watermark_end",
        "input_watermark_start",
        "plan_sha256",
        "source_revision_sha256",
        "source_schema_sha256",
        "target_event_row_set_sha256",
        "target_object_row_set_sha256",
        "target_schema_sha256",
    ):
        try:
            validate_sha256_digest(value[field])  # type: ignore[arg-type]
        except (KeyError, RawV2ContractError, TypeError) as error:
            raise RawV2ContractError(
                RawV2Reason.MIGRATION_COUNT_MISMATCH,
                f"migration artifact {field} is invalid",
            ) from error


def _semantic_status(
    *,
    invariants: Sequence[Mapping[str, object]],
    findings: Sequence[Mapping[str, object]],
    reingest: Mapping[str, int],
    owner_mapping: Mapping[str, int],
) -> MigrationArtifactStatus:
    if any(item["passed"] is not True for item in invariants) or any(
        item["severity"] == MigrationSeverity.P0.value and int(item["count"]) > 0
        for item in findings
    ):
        return MigrationArtifactStatus.DRY_RUN_FAIL
    hold = bool(sum(reingest.values()) or sum(owner_mapping.values())) or any(
        str(item["category"]) in _HOLD_CATEGORIES and int(item["count"]) > 0 for item in findings
    )
    return MigrationArtifactStatus.DRY_RUN_HOLD if hold else MigrationArtifactStatus.DRY_RUN_PASS


def _validate_semantics(value: dict[str, object]) -> MigrationArtifactStatus:
    if set(value) != _TOP_LEVEL_FIELDS:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration artifact field set changed")
    if (
        value["schema_version"] != MIGRATION_DRY_RUN_ARTIFACT_SCHEMA_VERSION
        or value["artifact_version"] != MIGRATION_DRY_RUN_ARTIFACT_VERSION
        or value["mode"] != MigrationInputMode.FIXTURE.value
        or value["production_database_accessed"] is not False
        or type(value["stable_input"]) is not bool
        or type(value["rollback_ready"]) is not bool
        or value["commands"] != []
        or value["limitations"] != list(_LIMITATIONS)
    ):
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration artifact envelope changed")
    _validate_digest_fields(value)
    unsigned = dict(value)
    claimed = unsigned.pop("content_sha256")
    if _digest(unsigned) != claimed:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration artifact content digest changed")

    table_counts = _mapping_counts(
        value["table_counts_before"],
        exact_keys=_TABLE_COUNT_FIELDS,
        label="table_counts_before",
    )
    projected = _mapping_counts(
        value["projected_counts_after"],
        exact_keys=_PROJECTED_COUNT_FIELDS,
        label="projected_counts_after",
    )
    classification = _mapping_counts(
        value["classification_counts"],
        exact_keys=tuple(item.value for item in MigrationCategory),
        label="classification_counts",
    )
    reingest = _mapping_counts(
        value["reingest_required_counts"],
        label="reingest_required_counts",
    )
    owner_mapping = _mapping_counts(
        value["owner_mapping_required_counts"],
        label="owner_mapping_required_counts",
    )
    allowed_categories = {item.value for item in MigrationCategory}
    if not set(reingest).issubset(allowed_categories) or not set(owner_mapping).issubset(
        allowed_categories
    ):
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration resolution category changed")

    groups_raw = value["counts_by_project_source_state"]
    if type(groups_raw) is not list:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration scope counts changed")
    groups: list[dict[str, object]] = []
    prior_scope = ""
    for item in groups_raw:
        if type(item) is not dict or set(item) != {"count", "scope_sha256"}:
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration scope count field changed")
        scope = validate_sha256_digest(item["scope_sha256"])  # type: ignore[arg-type]
        count = _exact_nonnegative_int(item["count"], label="migration scope count")
        if count == 0 or scope <= prior_scope:
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration scope counts are not canonical")
        prior_scope = scope
        groups.append({"count": count, "scope_sha256": scope})
    if sum(int(item["count"]) for item in groups) != projected["v2_object_total"]:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration scope counts do not conserve")

    findings_raw = value["finding_counts_by_severity_category"]
    if type(findings_raw) is not list:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration finding counts changed")
    findings: list[dict[str, object]] = []
    prior_finding: tuple[str, str] | None = None
    finding_lookup: Counter[str] = Counter()
    for item in findings_raw:
        if type(item) is not dict or set(item) != {"category", "count", "severity"}:
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration finding field set changed")
        category = _exact_string(item["category"], label="finding category")
        severity = _exact_string(item["severity"], label="finding severity")
        count = _exact_nonnegative_int(item["count"], label="finding count")
        pair = (severity, category)
        if (
            category not in allowed_categories
            or severity not in {item.value for item in MigrationSeverity}
            or count == 0
            or (prior_finding is not None and pair <= prior_finding)
        ):
            _fail(
                RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration finding counts are not canonical"
            )
        prior_finding = pair
        finding_lookup[category] += count
        findings.append({"category": category, "count": count, "severity": severity})
    if sum(int(item["count"]) for item in findings) != sum(table_counts.values()):
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration finding counts do not conserve")
    if sum(classification.values()) != table_counts["v1_raw_total"]:
        _fail(
            RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration raw classification does not conserve"
        )
    for category, count in {**reingest, **owner_mapping}.items():
        if count > finding_lookup[category]:
            _fail(
                RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration resolution count exceeds findings"
            )

    invariants_raw = value["invariant_results"]
    if type(invariants_raw) is not list or len(invariants_raw) != len(_INVARIANT_IDS):
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration invariant set changed")
    invariants: list[dict[str, object]] = []
    for expected_id, item in zip(_INVARIANT_IDS, invariants_raw, strict=True):
        if type(item) is not dict or set(item) != {"expected", "id", "observed", "passed"}:
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration invariant field set changed")
        if item["id"] != expected_id or type(item["passed"]) is not bool:
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration invariant identity changed")
        if item["passed"] != (item["observed"] == item["expected"]):
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration invariant result changed")
        invariants.append(item)
    required_pairs = (
        (sum(classification.values()), table_counts["v1_raw_total"]),
        (sum(finding_lookup.values()), sum(table_counts.values())),
        (projected["v2_object_total"], projected["copied_safe_rows"]),
        (
            projected["active_v2_object_total"],
            projected["available_verified_blob_total"],
        ),
        (projected["binding_total"], projected["selector_verified_rows"]),
        (0, 0),
    )
    for index, (observed, expected) in enumerate(required_pairs):
        if invariants[index]["observed"] != observed or invariants[index]["expected"] != expected:
            _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration invariant/count binding changed")
    if (
        invariants[9]["observed"] is not value["stable_input"]
        or invariants[9]["expected"] is not True
    ):
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration stable-input invariant changed")
    if value["rollback_ready"] is not value["stable_input"]:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration rollback readiness changed")

    status = _semantic_status(
        invariants=invariants,
        findings=findings,
        reingest=reingest,
        owner_mapping=owner_mapping,
    )
    if value["status"] != status.value:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration artifact status changed")
    blockers_raw = value["blockers"]
    if type(blockers_raw) is not list or any(type(item) is not str for item in blockers_raw):
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration blocker set changed")
    expected_blockers = _blockers(status=status, invariants=invariants, findings=findings)
    if blockers_raw != expected_blockers:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration blockers do not match status")

    serialized = _canonical_bytes(value).decode("utf-8")
    if "://" in serialized or "\\" in serialized or "\u0000" in serialized:
        _fail(RawV2Reason.MIGRATION_COUNT_MISMATCH, "migration artifact exposes a forbidden value")
    return status


class RawV1MigrationArtifactVerifier:
    """Verify canonical migration artifact bytes without database or blob I/O."""

    @staticmethod
    def verify_bytes(
        payload: bytes,
        *,
        expected_source_snapshot_sha256: str | None = None,
    ) -> MigrationArtifactVerification:
        value = _parse_artifact(payload)
        status = _validate_semantics(value)
        source_snapshot = validate_sha256_digest(value["input_snapshot_sha256"])  # type: ignore[arg-type]
        if expected_source_snapshot_sha256 is not None:
            expected = validate_sha256_digest(expected_source_snapshot_sha256)
            if source_snapshot != expected:
                _fail(RawV2Reason.MIGRATION_INPUT_CHANGED, "artifact input snapshot changed")
        invariant_digest = _digest(value["invariant_results"])
        content_digest = str(value["content_sha256"])
        verification_digest = _digest(
            {
                "content_sha256": content_digest,
                "input_snapshot_sha256": source_snapshot,
                "invariant_result_sha256": invariant_digest,
                "status": status.value,
            }
        )
        return MigrationArtifactVerification(
            status=status,
            content_sha256=content_digest,
            source_snapshot_sha256=source_snapshot,
            invariant_result_sha256=invariant_digest,
            verification_sha256=verification_digest,
        )

    @staticmethod
    def verify_file(
        path: Path,
        *,
        expected_source_snapshot_sha256: str | None = None,
    ) -> MigrationArtifactVerification:
        if not isinstance(path, Path):
            raise TypeError("artifact path must be a pathlib.Path")
        return RawV1MigrationArtifactVerifier.verify_bytes(
            path.read_bytes(),
            expected_source_snapshot_sha256=expected_source_snapshot_sha256,
        )
