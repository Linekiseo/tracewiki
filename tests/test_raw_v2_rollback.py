from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.sources.schema import SCHEMA as V1_SCHEMA
from evidence_rag.sources.v2.contracts import (
    SourceDomain,
    canonical_json_bytes,
    exact_bytes_sha256,
)
from evidence_rag.sources.v2.migration_artifact_v1 import RawV1MigrationReconciler
from evidence_rag.sources.v2.migration_v1 import (
    MigrationDerivedEntityClaim,
    MigrationInputBinding,
    MigrationSafeCopyPlan,
    MigrationSafeCopyResult,
    MigrationScanReport,
    MigrationSourceObjectClaim,
    MigrationStableVersionClaim,
    RawV1MigrationScanner,
    RawV1SafeCopier,
)
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.rollback_v1 import (
    RAW_V2_ROLLBACK_DRILL_VERSION,
    RAW_V2_ROLLBACK_MATRIX_ARTIFACT_VERSION,
    RAW_V2_ROLLBACK_MATRIX_SCHEMA_VERSION,
    RawV1AuthoritySnapshot,
    RawV2RollbackDrill,
    RawV2RollbackError,
    RawV2RollbackErrorCode,
    RawV2RollbackInputMode,
    RawV2RollbackMatrix,
    RawV2RollbackResult,
    RawV2RollbackStage,
    RawV2RollbackStatus,
    snapshot_v1_authority,
)
from evidence_rag.sources.v2.runtime_control import (
    RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION,
    RawV2AuthorityState,
    RawV2ObservationKind,
    RawV2ReadMode,
    RawV2RuntimeControl,
    RawV2RuntimePolicy,
    RawV2RuntimeScope,
    RawV2ShadowProjection,
    RawV2WriteMode,
    load_reviewed_runtime_policy,
)
from evidence_rag.sources.v2.schema import (
    RAW_V2_INDEXES,
    RAW_V2_TABLES,
    initialize_raw_v2_schema,
)

TS = "2026-01-01T00:00:00.000000Z"
SOURCE_REVISION = "source-revision-fixture-v1"
V24_REVISION = "2a86762e10e8df244a762fda02f060dc62fb84ad"
ARTIFACT_SALT = b"rollback-fixture-artifact-salt-v1"
STAGES = tuple(RawV2RollbackStage)


def _sha(payload: bytes | str) -> str:
    raw = payload.encode() if isinstance(payload, str) else payload
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _opener(path: Path) -> Callable[[], sqlite3.Connection]:
    return lambda: sqlite3.connect(path)


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


def _project_policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


def _scope() -> RawV2RuntimeScope:
    return RawV2RuntimeScope(
        project_id="project-private",
        source_domain=SourceDomain.CODE,
        intent="migration-drill",
    )


def _runtime_policy(stage: RawV2RollbackStage) -> RawV2RuntimePolicy:
    read_mode = "v1" if stage is RawV2RollbackStage.M6_DUAL_WRITE_DARK else "v2-shadow"
    payload = canonical_json_bytes(
        {
            "reviewed_revision": V24_REVISION,
            "rules": [
                {
                    "intent": "migration-drill",
                    "project_id": "project-private",
                    "read_mode": read_mode,
                    "source_domain": "code",
                    "write_mode": "dual-write-dark",
                }
            ],
            "schema_version": RAW_V2_RUNTIME_CONTROL_POLICY_SCHEMA_VERSION,
        }
    )
    return load_reviewed_runtime_policy(
        payload,
        expected_content_sha256=exact_bytes_sha256(payload),
        expected_revision=V24_REVISION,
    )


def _projection(label: str) -> RawV2ShadowProjection:
    return RawV2ShadowProjection(
        object_authority_sha256=_sha(f"object-{label}"),
        binding_authority_sha256=_sha(f"binding-{label}"),
        raw_content_sha256=_sha(f"content-{label}"),
        state=RawV2AuthorityState.ACTIVE,
        source_version_sha256=_sha(f"version-{label}"),
        acl_partition_sha256=_sha(f"acl-{label}"),
        reason=None,
    )


@dataclasses.dataclass(frozen=True)
class StageFixture:
    stage: RawV2RollbackStage
    source_db: Path
    target_db: Path
    target_blob_root: Path
    m0: RawV1AuthoritySnapshot
    migration_binding: MigrationInputBinding | None
    scan_report: MigrationScanReport | None
    copy_plan: MigrationSafeCopyPlan | None
    copy_result: MigrationSafeCopyResult | None
    migration_artifact: bytes | None
    runtime_policy: RawV2RuntimePolicy | None
    runtime_scope: RawV2RuntimeScope | None
    retained_evidence_sha256: str | None
    pre_rollback_v2_calls: int
    secrets: tuple[str, ...]


def _create_v1_database(path: Path, payload_path: Path, payload: bytes) -> RawV1AuthoritySnapshot:
    connection = sqlite3.connect(path)
    connection.executescript(V1_SCHEMA)
    payload_sha256 = _sha(payload)
    connection.execute(
        """INSERT INTO raw_objects(
               id, project_id, source_type, source_instance, source_object_id,
               source_version, source_uri, content_hash, media_type, byte_length,
               storage_path, acl_ref, state, adapter_version, schema_version,
               metadata_json, observed_at, tombstoned_at
           ) VALUES ('raw-private', 'project-private', 'git', 'repo-private',
                     'object-private', 'source-version-private', 'source://private/object', ?,
                     'application/octet-stream', ?, ?, 'acl-private', 'active',
                     'adapter-private', 'schema-private', '{"secret":"metadata-private"}', ?, NULL)""",
        (payload_sha256, len(payload), str(payload_path), TS),
    )
    connection.execute(
        """INSERT INTO source_events(
               event_id, idempotency_key, source_type, source_instance, event_type,
               source_object_id, source_version, event_time, observed_at, project_id,
               acl_ref, content_hash, raw_object_id, payload_ref, trace_id,
               schema_version, status, metadata_json, created_at
           ) VALUES ('event-private', 'mutation-private', 'git', 'repo-private', 'observed',
                     'object-private', 'source-version-private', ?, ?, 'project-private',
                     'acl-private', ?, 'raw-private', 'payload://private/object',
                     'trace-private', 'schema-private', 'persisted',
                     '{"secret":"event-private"}', ?)""",
        (TS, TS, payload_sha256, TS),
    )
    connection.execute(
        """INSERT INTO raw_derivations(
               raw_object_id, derived_entity_id, derived_kind, generation_id,
               derivation_version, created_at, invalidated_at
           ) VALUES ('raw-private', 'entity-private', 'chunk', 'generation-private',
                     'derivation-private', ?, NULL)""",
        (TS,),
    )
    connection.commit()
    snapshot = snapshot_v1_authority(connection)
    connection.close()
    return snapshot


def _stage_fixture(
    root: Path,
    *,
    stage: RawV2RollbackStage,
    shared_payload_path: Path,
) -> StageFixture:
    root.mkdir(parents=True)
    payload = shared_payload_path.read_bytes()
    source_db = root / "source.sqlite3"
    m0 = _create_v1_database(source_db, shared_payload_path, payload)
    rank = STAGES.index(stage) + 1

    binding: MigrationInputBinding | None = None
    report: MigrationScanReport | None = None
    plan: MigrationSafeCopyPlan | None = None
    result: MigrationSafeCopyResult | None = None
    artifact: bytes | None = None
    policy: RawV2RuntimePolicy | None = None
    scope: RawV2RuntimeScope | None = None
    retained_evidence: str | None = None
    pre_v2_calls = 0

    if rank >= 2:
        source = sqlite3.connect(source_db)
        binding = RawV1MigrationScanner.freeze_fixture(
            connection=source,
            dataset_id="rollback-private-fixture",
            source_revision=SOURCE_REVISION,
            blob_root=shared_payload_path.parent,
            source_claims=(
                MigrationSourceObjectClaim(
                    project_id="project-private",
                    acl_ref="acl-private",
                    source_type="git",
                    source_instance="repo-private",
                    source_object_id="object-private",
                    source_version="source-version-private",
                    content_hash=_sha(payload),
                ),
            ),
        )
        source.close()
    if rank >= 3:
        assert binding is not None
        report = RawV1MigrationScanner.scan(binding=binding, opener=_opener(source_db))

    target_blob_root = root / "target-blobs"
    target_blob_root.mkdir()
    target_db = root / "target.sqlite3"
    target = sqlite3.connect(target_db)
    target_schema = initialize_raw_v2_schema(target)

    if rank >= 4:
        assert binding is not None and report is not None
        raw_fingerprint = next(
            item.row_fingerprint for item in report.classifications if item.table == "raw_objects"
        )
        derived_fingerprint = next(
            item.row_fingerprint
            for item in report.classifications
            if item.table == "raw_derivations"
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
        result = RawV1SafeCopier.copy_fixture(
            binding=binding,
            scan_report=report,
            plan=plan,
            source_opener=_opener(source_db),
            target_connection=target,
            target_blob_root=target_blob_root,
            policy=_project_policy(),
            clock=lambda: TS,
        )
    if rank >= 5:
        assert (
            binding is not None and report is not None and plan is not None and result is not None
        )
        artifact = RawV1MigrationReconciler.build_fixture_artifact(
            binding=binding,
            scan_report=report,
            copy_plan=plan,
            copy_result=result,
            target_connection=target,
            target_blob_root=target_blob_root,
            artifact_salt=ARTIFACT_SALT,
        )
    target.close()

    if rank >= 6:
        policy = _runtime_policy(stage)
        scope = _scope()
        calls: list[str] = []
        control = RawV2RuntimeControl(policy)
        if stage is RawV2RollbackStage.M6_DUAL_WRITE_DARK:
            runtime_result = control.execute_write(
                scope=scope,
                v1=lambda: calls.append("v1") or object(),
                v2=lambda: calls.append("v2") or object(),
                project_v1=lambda _value: _projection("same"),
                project_v2=lambda _value: _projection("same"),
            )
            assert runtime_result.observation.kind is RawV2ObservationKind.DUAL_WRITE_MATCH
        else:
            runtime_result = control.execute_read(
                scope=scope,
                v1=lambda: calls.append("v1") or object(),
                v2=lambda: calls.append("v2") or object(),
                project_v1=lambda _value: _projection("v1"),
                project_v2=lambda _value: _projection("v2"),
            )
            assert runtime_result.observation.kind is RawV2ObservationKind.SHADOW_MISMATCH
        assert calls == ["v1", "v2"]
        pre_v2_calls = calls.count("v2")
        retained_evidence = exact_bytes_sha256(runtime_result.observation.canonical_bytes())

    return StageFixture(
        stage=stage,
        source_db=source_db,
        target_db=target_db,
        target_blob_root=target_blob_root,
        m0=m0,
        migration_binding=binding,
        scan_report=report,
        copy_plan=plan,
        copy_result=result,
        migration_artifact=artifact,
        runtime_policy=policy,
        runtime_scope=scope,
        retained_evidence_sha256=retained_evidence,
        pre_rollback_v2_calls=pre_v2_calls,
        secrets=(
            "project-private",
            "acl-private",
            "repo-private",
            "source://private/object",
            "payload://private/object",
            "alpha-private-payload",
            str(source_db),
            str(target_db),
            str(target_blob_root),
        ),
    )


def _freeze(fixture: StageFixture):
    return RawV2RollbackDrill.freeze(
        input_mode=RawV2RollbackInputMode.FIXTURE,
        stage=fixture.stage,
        m0_v1_authority=fixture.m0,
        v1_opener=_opener(fixture.source_db),
        v2_opener=_opener(fixture.target_db),
        v2_blob_root=fixture.target_blob_root,
        migration_binding=fixture.migration_binding,
        scan_report=fixture.scan_report,
        copy_plan=fixture.copy_plan,
        copy_result=fixture.copy_result,
        migration_artifact=fixture.migration_artifact,
        runtime_policy=fixture.runtime_policy,
        runtime_scope=fixture.runtime_scope,
        retained_evidence_sha256=fixture.retained_evidence_sha256,
    )


def _execute(fixture: StageFixture, binding: Any):
    trace: list[str] = []

    def traced_v1() -> sqlite3.Connection:
        connection = sqlite3.connect(fixture.source_db)
        connection.set_trace_callback(trace.append)
        return connection

    execution = RawV2RollbackDrill.execute(
        binding=binding,
        v1_opener=traced_v1,
        v2_opener=_opener(fixture.target_db),
    )
    mutating_v1 = tuple(
        statement
        for statement in trace
        if statement.lstrip()
        .upper()
        .startswith(("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "CREATE", "REPLACE"))
    )
    assert mutating_v1 == ()
    return execution


def _assert_default_policy(policy: RawV2RuntimePolicy) -> None:
    modes = policy.modes_for(_scope())
    assert modes.write_mode is RawV2WriteMode.V1_ONLY
    assert modes.read_mode is RawV2ReadMode.V1
    calls: list[str] = []
    control = RawV2RuntimeControl(policy)
    sentinel = object()
    write = control.execute_write(
        scope=_scope(),
        v1=lambda: calls.append("write-v1") or sentinel,
        v2=lambda: calls.append("write-v2") or object(),
        project_v1=lambda _value: _projection("write-v1"),
        project_v2=lambda _value: _projection("write-v2"),
    )
    read = control.execute_read(
        scope=_scope(),
        v1=lambda: calls.append("read-v1") or sentinel,
        v2=lambda: calls.append("read-v2") or object(),
        project_v1=lambda _value: _projection("read-v1"),
        project_v2=lambda _value: _projection("read-v2"),
    )
    assert write.product_result is sentinel and read.product_result is sentinel
    assert calls == ["write-v1", "read-v1"]


def _scoped_v2_objects(path: Path) -> int:
    connection = sqlite3.connect(path)
    names = (*RAW_V2_TABLES, *RAW_V2_INDEXES)
    placeholders = ",".join("?" for _ in names)
    tables = ",".join("?" for _ in RAW_V2_TABLES)
    count = int(
        connection.execute(
            f"SELECT COUNT(*) FROM sqlite_master WHERE name IN ({placeholders}) OR tbl_name IN ({tables})",
            (*names, *RAW_V2_TABLES),
        ).fetchone()[0]
    )
    connection.close()
    return count


def _run_matrix(tmp_path: Path) -> tuple[bytes, tuple[RawV2RollbackResult, ...], tuple[str, ...]]:
    shared = tmp_path / "shared-source"
    shared.mkdir(parents=True)
    payload_path = shared / "raw-private.bin"
    payload_path.write_bytes(b"alpha-private-payload")
    results: list[RawV2RollbackResult] = []
    secrets: list[str] = []
    m0_digests: set[str] = set()
    for stage in STAGES:
        fixture = _stage_fixture(
            tmp_path / stage.value.lower(), stage=stage, shared_payload_path=payload_path
        )
        binding = _freeze(fixture)
        execution = _execute(fixture, binding)
        result = execution.result
        results.append(result)
        secrets.extend(fixture.secrets)
        m0_digests.add(fixture.m0.authority_sha256)
        assert result.status is RawV2RollbackStatus.ROLLBACK_PASS
        assert result.v1_before_authority_sha256 == fixture.m0.authority_sha256
        assert result.v1_after_authority_sha256 == fixture.m0.authority_sha256
        assert result.v2_after_scoped_object_count == 0
        assert result.v2_after_blob_file_count == 0
        assert result.reverse_v1_write_count == 0
        assert result.formal_table_drop_count == 0
        assert _scoped_v2_objects(fixture.target_db) == 0
        assert fixture.target_blob_root.exists() and tuple(fixture.target_blob_root.iterdir()) == ()
        source = sqlite3.connect(fixture.source_db)
        assert snapshot_v1_authority(source) == fixture.m0
        source.close()
        _assert_default_policy(execution.policy)
        if STAGES.index(stage) >= 5:
            assert fixture.pre_rollback_v2_calls == 1
            assert result.retained_evidence_sha256 == fixture.retained_evidence_sha256
        else:
            assert result.retained_evidence_sha256 is None
    assert len(m0_digests) == 1
    result_tuple = tuple(results)
    return RawV2RollbackMatrix.build(result_tuple), result_tuple, tuple(secrets)


def test_m1_m7_real_fixture_rollbacks_preserve_v1_and_disable_v2(tmp_path: Path) -> None:
    artifact, results, secrets = _run_matrix(tmp_path)
    verification = RawV2RollbackMatrix.verify_bytes(artifact)
    value = json.loads(artifact)

    assert RAW_V2_ROLLBACK_DRILL_VERSION == "raw-v2-fixture-rollback-drill-v1"
    assert value["schema_version"] == RAW_V2_ROLLBACK_MATRIX_SCHEMA_VERSION
    assert value["artifact_version"] == RAW_V2_ROLLBACK_MATRIX_ARTIFACT_VERSION
    assert value["status"] == "ROLLBACK_PASS"
    assert value["production_database_accessed"] is False
    assert len(value["stage_results"]) == 7
    assert [item["stage"] for item in value["stage_results"]] == [stage.value for stage in STAGES]
    assert all(item["passed"] is True for item in value["invariant_results"])
    assert verification.status is RawV2RollbackStatus.ROLLBACK_PASS
    assert len({item.rollback_policy_sha256 for item in results}) == 1
    serialized = artifact.decode()
    for secret in secrets:
        assert secret not in serialized
    with pytest.raises(FrozenInstanceError):
        results[0].write_disabled = False  # type: ignore[misc]


def test_matrix_copy_verify_and_tamper_rejection(tmp_path: Path) -> None:
    artifact, _results, _secrets = _run_matrix(tmp_path / "matrix")
    original = RawV2RollbackMatrix.verify_bytes(artifact)
    copied = tmp_path / "copied-matrix.json"
    copied.write_bytes(artifact)
    assert RawV2RollbackMatrix.verify_file(copied) == original

    duplicate = artifact.replace(
        b'{"artifact_version"', b'{"status":"ROLLBACK_PASS","artifact_version"', 1
    )
    noncanonical = artifact[:-1] + b" \n"
    value = json.loads(artifact)
    mutations: list[dict[str, Any]] = []
    missing_stage = json.loads(artifact)
    missing_stage["stage_results"].pop()
    mutations.append(missing_stage)
    duplicate_stage = json.loads(artifact)
    duplicate_stage["stage_results"][1] = duplicate_stage["stage_results"][0]
    mutations.append(duplicate_stage)
    wrong_v1 = json.loads(artifact)
    wrong_v1["stage_results"][0]["v1_after_authority_sha256"] = _sha("wrong")
    mutations.append(wrong_v1)
    wrong_policy = json.loads(artifact)
    wrong_policy["stage_results"][5]["rollback_policy_sha256"] = _sha("wrong")
    mutations.append(wrong_policy)
    discarded_evidence = json.loads(artifact)
    discarded_evidence["stage_results"][6]["retained_evidence_sha256"] = None
    mutations.append(discarded_evidence)
    false_invariant = json.loads(artifact)
    false_invariant["invariant_results"][0]["passed"] = False
    mutations.append(false_invariant)

    for payload in (duplicate, noncanonical):
        with pytest.raises(RawV2RollbackError) as captured:
            RawV2RollbackMatrix.verify_bytes(payload)
        assert captured.value.code is RawV2RollbackErrorCode.MATRIX_INVALID
    for mutation in mutations:
        tampered = canonical_json_bytes(mutation, allow_none=True) + b"\n"
        with pytest.raises(RawV2RollbackError) as captured:
            RawV2RollbackMatrix.verify_bytes(tampered)
        assert captured.value.code is RawV2RollbackErrorCode.MATRIX_INVALID
    assert value["content_sha256"] == original.content_sha256


def test_nonfixture_mode_rejects_before_openers_or_filesystem(tmp_path: Path) -> None:
    source_db = tmp_path / "source.sqlite3"
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    m0 = _create_v1_database(source_db, payload, b"payload")
    calls: list[str] = []

    def forbidden(label: str) -> Callable[[], sqlite3.Connection]:
        def open_forbidden() -> sqlite3.Connection:
            calls.append(label)
            raise AssertionError("nonfixture mode must reject before opener")

        return open_forbidden

    for mode in (
        RawV2RollbackInputMode.MIRROR,
        RawV2RollbackInputMode.AUTHORIZED_PRODUCTION,
    ):
        with pytest.raises(RawV2RollbackError) as captured:
            RawV2RollbackDrill.freeze(
                input_mode=mode,
                stage=RawV2RollbackStage.M1_SCHEMA_PREPARED,
                m0_v1_authority=m0,
                v1_opener=forbidden("v1"),
                v2_opener=forbidden("v2"),
                v2_blob_root=tmp_path / "absent",
            )
        assert captured.value.code is RawV2RollbackErrorCode.NOT_AUTHORIZED
    assert calls == []


def test_v1_drift_rejects_before_v2_cleanup(tmp_path: Path) -> None:
    payload_root = tmp_path / "shared"
    payload_root.mkdir()
    payload = payload_root / "payload.bin"
    payload.write_bytes(b"alpha-private-payload")
    fixture = _stage_fixture(
        tmp_path / "stage",
        stage=RawV2RollbackStage.M1_SCHEMA_PREPARED,
        shared_payload_path=payload,
    )
    binding = _freeze(fixture)
    source = sqlite3.connect(fixture.source_db)
    source.execute("UPDATE raw_objects SET metadata_json='{}' WHERE id='raw-private'")
    source.commit()
    source.close()
    with pytest.raises(RawV2RollbackError) as captured:
        _execute(fixture, binding)
    assert captured.value.code is RawV2RollbackErrorCode.V1_AUTHORITY_CHANGED
    assert _scoped_v2_objects(fixture.target_db) == 25


def test_v2_schema_drift_rejects_before_cleanup(tmp_path: Path) -> None:
    payload_root = tmp_path / "shared"
    payload_root.mkdir()
    payload = payload_root / "payload.bin"
    payload.write_bytes(b"alpha-private-payload")
    fixture = _stage_fixture(
        tmp_path / "stage",
        stage=RawV2RollbackStage.M1_SCHEMA_PREPARED,
        shared_payload_path=payload,
    )
    binding = _freeze(fixture)
    target = sqlite3.connect(fixture.target_db)
    target.execute("DROP INDEX idx_raw_v2_lookup")
    target.commit()
    target.close()
    with pytest.raises(RawV2RollbackError) as captured:
        _execute(fixture, binding)
    assert captured.value.code is RawV2RollbackErrorCode.V2_FIXTURE_INVALID
    assert _scoped_v2_objects(fixture.target_db) == 24
    source = sqlite3.connect(fixture.source_db)
    assert snapshot_v1_authority(source) == fixture.m0
    source.close()


@pytest.mark.parametrize("mutation", ["extra", "missing", "symlink"])
def test_blob_drift_rejects_before_cleanup(tmp_path: Path, mutation: str) -> None:
    payload_root = tmp_path / "shared"
    payload_root.mkdir()
    payload = payload_root / "payload.bin"
    payload.write_bytes(b"alpha-private-payload")
    fixture = _stage_fixture(
        tmp_path / "stage",
        stage=RawV2RollbackStage.M4_SAFE_ROWS_COPIED,
        shared_payload_path=payload,
    )
    binding = _freeze(fixture)
    files = [path for path in fixture.target_blob_root.rglob("*") if path.is_file()]
    assert len(files) == 1
    if mutation == "extra":
        (fixture.target_blob_root / "extra.bin").write_bytes(b"extra")
    elif mutation == "missing":
        files[0].unlink()
    else:
        (fixture.target_blob_root / "linked.bin").symlink_to(payload)
    before = _scoped_v2_objects(fixture.target_db)
    with pytest.raises(RawV2RollbackError) as captured:
        _execute(fixture, binding)
    assert captured.value.code in {
        RawV2RollbackErrorCode.V2_FIXTURE_INVALID,
        RawV2RollbackErrorCode.V2_BLOB_INVALID,
    }
    assert _scoped_v2_objects(fixture.target_db) == before
    source = sqlite3.connect(fixture.source_db)
    assert snapshot_v1_authority(source) == fixture.m0
    source.close()


def test_stage_requires_exact_evidence_chain_and_modes(tmp_path: Path) -> None:
    payload_root = tmp_path / "shared"
    payload_root.mkdir()
    payload = payload_root / "payload.bin"
    payload.write_bytes(b"alpha-private-payload")
    m6 = _stage_fixture(
        tmp_path / "m6",
        stage=RawV2RollbackStage.M6_DUAL_WRITE_DARK,
        shared_payload_path=payload,
    )
    with pytest.raises(RawV2RollbackError) as missing:
        RawV2RollbackDrill.freeze(
            input_mode=RawV2RollbackInputMode.FIXTURE,
            stage=RawV2RollbackStage.M6_DUAL_WRITE_DARK,
            m0_v1_authority=m6.m0,
            v1_opener=_opener(m6.source_db),
            v2_opener=_opener(m6.target_db),
            v2_blob_root=m6.target_blob_root,
        )
    assert missing.value.code is RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID

    wrong_policy = _runtime_policy(RawV2RollbackStage.M7_V2_SHADOW_READ)
    with pytest.raises(RawV2RollbackError) as wrong_mode:
        RawV2RollbackDrill.freeze(
            input_mode=RawV2RollbackInputMode.FIXTURE,
            stage=RawV2RollbackStage.M6_DUAL_WRITE_DARK,
            m0_v1_authority=m6.m0,
            v1_opener=_opener(m6.source_db),
            v2_opener=_opener(m6.target_db),
            v2_blob_root=m6.target_blob_root,
            migration_binding=m6.migration_binding,
            scan_report=m6.scan_report,
            copy_plan=m6.copy_plan,
            copy_result=m6.copy_result,
            migration_artifact=m6.migration_artifact,
            runtime_policy=wrong_policy,
            runtime_scope=m6.runtime_scope,
            retained_evidence_sha256=m6.retained_evidence_sha256,
        )
    assert wrong_mode.value.code is RawV2RollbackErrorCode.STAGE_EVIDENCE_INVALID
    assert _scoped_v2_objects(m6.target_db) == 25


def test_snapshot_detects_v1_schema_and_data_drift(tmp_path: Path) -> None:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(b"payload")
    first_db = tmp_path / "first.sqlite3"
    second_db = tmp_path / "second.sqlite3"
    first = _create_v1_database(first_db, payload, b"payload")
    second = _create_v1_database(second_db, payload, b"payload")
    assert first == second

    connection = sqlite3.connect(second_db)
    connection.execute("UPDATE raw_objects SET metadata_json='{}' WHERE id='raw-private'")
    connection.commit()
    assert snapshot_v1_authority(connection).data_sha256 != first.data_sha256
    connection.execute("CREATE INDEX extra_v1_index ON raw_objects(project_id)")
    connection.commit()
    assert snapshot_v1_authority(connection).schema_sha256 != first.schema_sha256
    connection.close()


def test_matrix_verifier_is_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact, _results, _secrets = _run_matrix(tmp_path / "source")
    copied = tmp_path / "matrix.json"
    copied.write_bytes(artifact)

    def forbidden_connect(*_args: Any, **_kwargs: Any) -> sqlite3.Connection:
        raise AssertionError("offline verifier must not connect")

    monkeypatch.setattr(sqlite3, "connect", forbidden_connect)
    assert RawV2RollbackMatrix.verify_file(copied).status is RawV2RollbackStatus.ROLLBACK_PASS
