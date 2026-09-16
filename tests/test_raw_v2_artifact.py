from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.sources.schema import SCHEMA as V1_SCHEMA
from evidence_rag.sources.v2.contracts import RawV2ContractError, RawV2Reason
from evidence_rag.sources.v2.migration_artifact_v1 import (
    MIGRATION_DRY_RUN_ARTIFACT_SCHEMA_VERSION,
    MIGRATION_DRY_RUN_ARTIFACT_VERSION,
    MigrationArtifactStatus,
    RawV1MigrationArtifactVerifier,
    RawV1MigrationReconciler,
)
from evidence_rag.sources.v2.migration_v1 import (
    MigrationDerivedEntityClaim,
    MigrationSafeCopyCheckpoint,
    MigrationSeverity,
    MigrationSourceObjectClaim,
    MigrationStableVersionClaim,
    RawV1MigrationScanner,
    RawV1SafeCopier,
)
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.schema import initialize_raw_v2_schema

TS = "2026-09-01T00:00:00.000000Z"
SOURCE_REVISION = "23a5a378eea8970cbc9a847e85290c8054a7828c"
ARTIFACT_SALT = b"private-artifact-salt"
V2_TABLES = (
    "raw_blobs_v2",
    "raw_objects_v2",
    "source_events_v2",
    "raw_evidence_bindings_v2",
    "blocked_entities_v2",
    "raw_migration_runs_v2",
    "raw_migration_findings_v2",
)
TOP_LEVEL_FIELDS = {
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


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class _Authority:
    def project_exists(self, *, project_id: str) -> bool:
        return project_id == "project-private"

    def resolve_object_acl_ref(self, *, project_id: str, source_acl_ref: str) -> str | None:
        if (project_id, source_acl_ref) == ("project-private", "acl-private"):
            return source_acl_ref
        return None

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        del principal_id
        if project_id == "project-private":
            return ("acl-private",)
        return None


def _policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


@dataclasses.dataclass(frozen=True)
class CompletedFixture:
    source_db: Path
    source_blob_root: Path
    target_db: Path
    target_blob_root: Path
    target_connection: sqlite3.Connection
    binding: Any
    report: Any
    plan: Any
    result: Any
    secrets: tuple[str, ...]


def _opener(path: Path) -> Callable[[], sqlite3.Connection]:
    return lambda: sqlite3.connect(path)


class _BoundaryStop(RuntimeError):
    pass


def _completed_fixture(
    tmp_path: Path,
    *,
    resume_after_first: bool = False,
    include_quarantined: bool = False,
) -> CompletedFixture:
    source_root = tmp_path / "source-blobs-private"
    source_root.mkdir(parents=True)
    payload = b"alpha-private-payload"
    payload_path = source_root / "raw-private.bin"
    payload_path.write_bytes(payload)
    payload_digest = _sha256(payload)
    source_db = tmp_path / "source-private.sqlite3"
    source = sqlite3.connect(source_db)
    source.executescript(V1_SCHEMA)
    source.execute(
        """INSERT INTO raw_objects(
               id, project_id, source_type, source_instance, source_object_id,
               source_version, source_uri, content_hash, media_type, byte_length,
               storage_path, acl_ref, state, adapter_version, schema_version,
               metadata_json, observed_at, tombstoned_at
           ) VALUES ('raw-private', 'project-private', 'git', 'repo-private',
                     'object-private', 'source-version-private', 'source://private/object', ?,
                     'application/octet-stream', ?, ?, 'acl-private', 'active',
                     'adapter-private', 'schema-private', '{"secret":"metadata-private"}', ?, NULL)""",
        (payload_digest, len(payload), str(payload_path), TS),
    )
    source.execute(
        """INSERT INTO source_events(
               event_id, idempotency_key, source_type, source_instance, event_type,
               source_object_id, source_version, event_time, observed_at, project_id,
               acl_ref, content_hash, raw_object_id, payload_ref, trace_id,
               schema_version, status, metadata_json, created_at
           ) VALUES ('event-private', 'mutation-private', 'git', 'repo-private', 'observed',
                     'object-private', 'source-version-private', ?, ?, 'project-private',
                     'acl-private', ?, 'raw-private', 'payload://private/object',
                     'trace-private', 'schema-private', 'persisted',
                     '{"secret":"event-metadata-private"}', ?)""",
        (TS, TS, payload_digest, TS),
    )
    source.execute(
        """INSERT INTO raw_derivations(
               raw_object_id, derived_entity_id, derived_kind, generation_id,
               derivation_version, created_at, invalidated_at
           ) VALUES ('raw-private', 'entity-private', 'chunk', 'generation-private',
                     'derivation-private', ?, NULL)""",
        (TS,),
    )
    if include_quarantined:
        source.execute(
            """INSERT INTO raw_objects(
                   id, project_id, source_type, source_instance, source_object_id,
                   source_version, source_uri, content_hash, media_type, byte_length,
                   storage_path, acl_ref, state, adapter_version, schema_version,
                   metadata_json, observed_at, tombstoned_at
               ) VALUES ('raw-quarantined-private', 'project-private', 'git',
                         'repo-private', 'object-quarantined-private',
                         'source-version-quarantined-private', 'source://private/quarantined',
                         ?, 'application/octet-stream', ?, ?, 'acl-private', 'quarantined',
                         'adapter-private', 'schema-private', '{}', ?, NULL)""",
            (payload_digest, len(payload), str(payload_path), TS),
        )
    source.commit()
    claim = MigrationSourceObjectClaim(
        project_id="project-private",
        acl_ref="acl-private",
        source_type="git",
        source_instance="repo-private",
        source_object_id="object-private",
        source_version="source-version-private",
        content_hash=payload_digest,
    )
    claims = [claim]
    if include_quarantined:
        claims.append(
            MigrationSourceObjectClaim(
                project_id="project-private",
                acl_ref="acl-private",
                source_type="git",
                source_instance="repo-private",
                source_object_id="object-quarantined-private",
                source_version="source-version-quarantined-private",
                content_hash=payload_digest,
            )
        )
    binding = RawV1MigrationScanner.freeze_fixture(
        connection=source,
        dataset_id="m5-private-fixture",
        source_revision=SOURCE_REVISION,
        blob_root=source_root,
        source_claims=tuple(claims),
    )
    source.close()
    report = RawV1MigrationScanner.scan(binding=binding, opener=_opener(source_db))

    target_root = tmp_path / "target-blobs-private"
    target_root.mkdir(parents=True)
    target_db = tmp_path / "target-private.sqlite3"
    target = sqlite3.connect(target_db)
    target_schema = initialize_raw_v2_schema(target)
    raw_fingerprint = next(
        item.row_fingerprint for item in report.classifications if item.table == "raw_objects"
    )
    derived_fingerprint = next(
        item.row_fingerprint for item in report.classifications if item.table == "raw_derivations"
    )
    plan = RawV1SafeCopier.freeze_copy_plan(
        binding=binding,
        scan_report=report,
        target_schema_sha256=target_schema.schema_sha256,
        batch_size=1,
        stable_version_claims=(
            MigrationStableVersionClaim(raw_fingerprint, "stable-version-private"),
        ),
        derived_entity_claims=(
            MigrationDerivedEntityClaim(derived_fingerprint, "project-private"),
        ),
    )

    def copy(
        observer: Callable[[int, MigrationSafeCopyCheckpoint], None] | None = None,
    ) -> Any:
        return RawV1SafeCopier.copy_fixture(
            binding=binding,
            scan_report=report,
            plan=plan,
            source_opener=_opener(source_db),
            target_connection=target,
            target_blob_root=target_root,
            policy=_policy(),
            clock=lambda: TS,
            batch_observer=observer,
        )

    if resume_after_first:

        def stop(_batch: int, _checkpoint: MigrationSafeCopyCheckpoint) -> None:
            raise _BoundaryStop("post-boundary stop")

        with pytest.raises(_BoundaryStop, match="post-boundary stop"):
            copy(stop)
    result = copy()
    return CompletedFixture(
        source_db=source_db,
        source_blob_root=source_root,
        target_db=target_db,
        target_blob_root=target_root,
        target_connection=target,
        binding=binding,
        report=report,
        plan=plan,
        result=result,
        secrets=(
            "project-private",
            "acl-private",
            "repo-private",
            "object-private",
            "raw-private",
            "source-version-private",
            "stable-version-private",
            "source://private/object",
            "payload://private/object",
            "trace-private",
            "metadata-private",
            "alpha-private-payload",
            str(source_db),
            str(source_root),
            str(target_db),
            str(target_root),
            ARTIFACT_SALT.decode(),
        ),
    )


def _rows(connection: sqlite3.Connection) -> dict[str, list[list[Any]]]:
    result: dict[str, list[list[Any]]] = {}
    for table in V2_TABLES:
        columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")]
        rows = connection.execute(
            f"SELECT {', '.join(columns)} FROM {table} ORDER BY {', '.join(columns)}"
        ).fetchall()
        result[table] = [list(row) for row in rows]
    return result


def _tree(root: Path) -> list[list[str]]:
    values: list[list[str]] = []
    for path in sorted(root.rglob("*")):
        kind = "symlink" if path.is_symlink() else "dir" if path.is_dir() else "file"
        digest = _sha256(path.read_bytes()) if kind == "file" else ""
        values.append([path.relative_to(root).as_posix(), kind, digest])
    return values


def _artifact(fixture: CompletedFixture) -> bytes:
    return RawV1MigrationReconciler.build_fixture_artifact(
        binding=fixture.binding,
        scan_report=fixture.report,
        copy_plan=fixture.plan,
        copy_result=fixture.result,
        target_connection=fixture.target_connection,
        target_blob_root=fixture.target_blob_root,
        artifact_salt=ARTIFACT_SALT,
    )


def _seal(value: dict[str, Any]) -> bytes:
    unsigned = dict(value)
    unsigned.pop("content_sha256", None)
    content = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    value = {**unsigned, "content_sha256": _sha256(content)}
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        + b"\n"
    )


def test_m5_artifact_is_canonical_private_conserved_and_resume_stable(tmp_path: Path) -> None:
    first = _completed_fixture(tmp_path / "first")
    before = (_rows(first.target_connection), _tree(first.target_blob_root))
    artifact = _artifact(first)
    after = (_rows(first.target_connection), _tree(first.target_blob_root))
    verification = RawV1MigrationArtifactVerifier.verify_bytes(
        artifact,
        expected_source_snapshot_sha256=first.binding.source_snapshot_sha256,
    )
    value = json.loads(artifact)

    assert before == after
    assert artifact.endswith(b"\n")
    assert set(value) == TOP_LEVEL_FIELDS
    assert value["schema_version"] == MIGRATION_DRY_RUN_ARTIFACT_SCHEMA_VERSION
    assert value["artifact_version"] == MIGRATION_DRY_RUN_ARTIFACT_VERSION
    assert value["status"] == MigrationArtifactStatus.DRY_RUN_PASS.value
    assert verification.status is MigrationArtifactStatus.DRY_RUN_PASS
    assert verification.content_sha256 == value["content_sha256"]
    assert value["production_database_accessed"] is False
    assert value["stable_input"] is True
    assert value["rollback_ready"] is True
    assert value["blockers"] == []
    assert value["commands"] == []
    assert all(item["passed"] is True for item in value["invariant_results"])
    assert value["table_counts_before"] == {
        "v1_blocked_entity_total": 0,
        "v1_derivation_total": 1,
        "v1_raw_total": 1,
        "v1_source_event_total": 1,
    }
    assert value["projected_counts_after"] == {
        "active_v2_object_total": 1,
        "available_verified_blob_total": 1,
        "binding_total": 0,
        "copied_event_total": 1,
        "copied_safe_rows": 1,
        "migration_run_total": 1,
        "selector_verified_rows": 0,
        "v2_blob_total": 1,
        "v2_event_total": 1,
        "v2_object_total": 1,
    }
    assert value["classification_counts"]["safe_copy"] == 1
    assert sum(value["classification_counts"].values()) == 1
    assert value["counts_by_project_source_state"] == [
        {"count": 1, "scope_sha256": value["counts_by_project_source_state"][0]["scope_sha256"]}
    ]
    assert value["counts_by_project_source_state"][0]["scope_sha256"].startswith("sha256:")
    artifact_text = artifact.decode()
    assert all(secret not in artifact_text for secret in first.secrets)

    independent = _completed_fixture(tmp_path / "independent")
    resumed = _completed_fixture(tmp_path / "resumed", resume_after_first=True)
    assert _artifact(independent) == artifact
    assert _artifact(resumed) == artifact


def test_non_p0_reingest_finding_is_hold_not_pass_or_fail(tmp_path: Path) -> None:
    fixture = _completed_fixture(tmp_path, include_quarantined=True)
    artifact = _artifact(fixture)
    verification = RawV1MigrationArtifactVerifier.verify_bytes(artifact)
    value = json.loads(artifact)

    assert verification.status is MigrationArtifactStatus.DRY_RUN_HOLD
    assert value["status"] == MigrationArtifactStatus.DRY_RUN_HOLD.value
    assert value["reingest_required_counts"] == {"quarantined_existing": 1}
    assert value["blockers"] == ["action-required:quarantined_existing"]
    assert all(item["passed"] is True for item in value["invariant_results"])


@pytest.mark.parametrize("mutation", ["missing_blob", "object_metadata", "ledger_counters"])
def test_reconciliation_reports_target_tamper_without_repair(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _completed_fixture(tmp_path / mutation)
    if mutation == "missing_blob":
        next(path for path in fixture.target_blob_root.rglob("*") if path.is_file()).unlink()
    elif mutation == "object_metadata":
        fixture.target_connection.execute(
            "UPDATE raw_objects_v2 SET metadata_json='{\"tampered\":true}'"
        )
        fixture.target_connection.commit()
    else:
        fixture.target_connection.execute(
            "UPDATE raw_migration_runs_v2 SET counters_json='{\"copied_object_count\":999}'"
        )
        fixture.target_connection.commit()
    before_rows = _rows(fixture.target_connection)
    before_tree = _tree(fixture.target_blob_root)

    artifact = _artifact(fixture)
    verification = RawV1MigrationArtifactVerifier.verify_bytes(artifact)
    value = json.loads(artifact)

    assert verification.status is MigrationArtifactStatus.DRY_RUN_FAIL
    assert value["status"] == MigrationArtifactStatus.DRY_RUN_FAIL.value
    assert value["blockers"]
    assert any(item["passed"] is False for item in value["invariant_results"])
    assert _rows(fixture.target_connection) == before_rows
    assert _tree(fixture.target_blob_root) == before_tree


@pytest.mark.parametrize("input_drift", ["unclassified", "p0"])
def test_unclassified_or_p0_stops_before_target_query(
    tmp_path: Path,
    input_drift: str,
) -> None:
    fixture = _completed_fixture(tmp_path / input_drift)
    if input_drift == "unclassified":
        report = dataclasses.replace(fixture.report, unclassified_count=1)
    else:
        first = dataclasses.replace(
            fixture.report.classifications[0], severity=MigrationSeverity.P0
        )
        report = dataclasses.replace(
            fixture.report,
            classifications=(first, *fixture.report.classifications[1:]),
        )
    trace: list[str] = []
    fixture.target_connection.set_trace_callback(trace.append)

    with pytest.raises(RawV2ContractError) as stopped:
        RawV1MigrationReconciler.build_fixture_artifact(
            binding=fixture.binding,
            scan_report=report,
            copy_plan=fixture.plan,
            copy_result=fixture.result,
            target_connection=fixture.target_connection,
            target_blob_root=fixture.target_blob_root,
            artifact_salt=ARTIFACT_SALT,
        )

    assert stopped.value.reason is RawV2Reason.MIGRATION_UNCLASSIFIED
    assert trace == []


def test_copy_verify_is_database_free_and_tamper_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _completed_fixture(tmp_path / "source")
    artifact = _artifact(fixture)
    original = json.loads(artifact)
    source_artifact = tmp_path / "artifact.json"
    source_artifact.write_bytes(artifact)
    copied = tmp_path / "relocated" / "copied-artifact.json"
    copied.parent.mkdir(parents=True)
    shutil.copyfile(source_artifact, copied)

    def forbidden_connect(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        raise AssertionError("verify-only must not connect to SQLite")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    direct = RawV1MigrationArtifactVerifier.verify_bytes(artifact)
    copied_result = RawV1MigrationArtifactVerifier.verify_file(copied)
    assert copied_result == direct

    duplicate = artifact.replace(
        b'{"artifact_version":',
        b'{"artifact_version":"duplicate","artifact_version":',
        1,
    )
    with pytest.raises(RawV2ContractError):
        RawV1MigrationArtifactVerifier.verify_bytes(duplicate)
    with pytest.raises(RawV2ContractError):
        RawV1MigrationArtifactVerifier.verify_bytes(b" " + artifact)

    wrong_count = json.loads(artifact)
    wrong_count["classification_counts"]["safe_copy"] = 2
    with pytest.raises(RawV2ContractError):
        RawV1MigrationArtifactVerifier.verify_bytes(_seal(wrong_count))
    wrong_status = json.loads(artifact)
    wrong_status["status"] = MigrationArtifactStatus.DRY_RUN_FAIL.value
    with pytest.raises(RawV2ContractError):
        RawV1MigrationArtifactVerifier.verify_bytes(_seal(wrong_status))
    wrong_blocker = json.loads(artifact)
    wrong_blocker["blockers"] = ["M5-99-invented"]
    with pytest.raises(RawV2ContractError):
        RawV1MigrationArtifactVerifier.verify_bytes(_seal(wrong_blocker))
    with pytest.raises(RawV2ContractError) as snapshot:
        RawV1MigrationArtifactVerifier.verify_bytes(
            artifact,
            expected_source_snapshot_sha256="sha256:" + "0" * 64,
        )
    assert snapshot.value.reason is RawV2Reason.MIGRATION_INPUT_CHANGED
    assert json.loads(artifact) == original
