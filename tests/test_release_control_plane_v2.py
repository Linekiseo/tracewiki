from __future__ import annotations

import importlib
import json
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest

import evidence_rag.rag.release_control_plane_v2 as control_plane_module
from evidence_rag.rag.release_admission_v2 import (
    ReleaseAdmissionPackageError,
    ReleaseDecision,
    build_current_repository_quality_hold_v2,
    inspect_current_production_authority_v2,
    write_release_admission_package_v2,
)
from evidence_rag.rag.release_control_plane_v2 import (
    CanonicalReleaseControlPlaneV2,
    ExactReleasePackageVerificationV2,
    ReleaseControlPlaneError,
    admit_immutable_reviewed_evidence_v2,
    canonical_release_control_plane_v2,
    current_release_status_v2,
    current_runtime_release_status_v2,
    runtime_release_status_from_operational_snapshot_v2,
    verify_exact_release_package_v2,
)
from evidence_rag.rag.sources.experiment.contracts_v2 import canonical_sha256_v2


def _quality_hold_package(tmp_path: Path) -> Path:
    authority = inspect_current_production_authority_v2()
    admission = build_current_repository_quality_hold_v2()
    return write_release_admission_package_v2(
        output_dir=tmp_path / "canonical-release-package",
        artifact_root=tmp_path,
        authority=authority,
        admission=admission,
    )


def _canonical_file(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def test_control_plane_is_unique_and_current_status_is_fail_closed() -> None:
    first = canonical_release_control_plane_v2()
    second = canonical_release_control_plane_v2()
    assert first is second
    assert (
        CanonicalReleaseControlPlaneV2(control_plane_module._CANONICAL_CONSTRUCTION_TOKEN) is first
    )
    with pytest.raises(ReleaseControlPlaneError, match="caller authorities"):
        CanonicalReleaseControlPlaneV2()

    status = current_release_status_v2()
    assert status == first.current_status()
    assert status.reviewed_source_count == 0
    assert status.reviewed_gate_count == 0
    assert status.authority_ready is False
    assert status.authority_attested is False
    assert status.authority_attestation_sha256 is None
    assert status.decision is ReleaseDecision.QUALITY_HOLD
    assert status.default_engine == "v1"
    assert status.quality_hold is True
    assert status.side_effect_free is True
    assert status.integrity_sha256.startswith("sha256:")
    assert "reviewed-release-authority-unavailable" in status.blockers


def test_non_current_production_authority_remains_default_v1_no_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_module = importlib.import_module("evidence_rag.rag.sources.document.runtime_v2")
    original = source_module.DocumentSourceRuntimeV2.search

    def replacement(*args: object, **kwargs: object) -> object:
        return original(*args, **kwargs)

    monkeypatch.setattr(source_module.DocumentSourceRuntimeV2, "search", replacement)
    status = current_release_status_v2()
    runtime = current_runtime_release_status_v2()

    assert status.production_authority_current is False
    assert "production-authority-not-current" in status.blockers
    assert status.reviewed_source_count == 0
    assert status.reviewed_gate_count == 0
    assert status.default_engine == "v1"
    assert status.decision is ReleaseDecision.QUALITY_HOLD
    assert status.quality_hold is True
    assert runtime.sources.code.v1_stage == "DEFAULT_V1"
    assert runtime.sources.code.v2_stage == "NO_RELEASE"
    assert runtime.sources.code.quality_state == "QUALITY_HOLD"


def test_current_status_and_admission_do_not_access_database_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("control plane attempted an external side effect")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert current_release_status_v2().side_effect_free is True
    admitted = admit_immutable_reviewed_evidence_v2()
    assert admitted.verify_only is True
    assert admitted.reviewed_evidence_admitted is False
    assert admitted.authority_attested is False
    assert admitted.authority_attestation_sha256 is None
    assert admitted.decision is ReleaseDecision.QUALITY_HOLD
    assert admitted.default_engine == "v1"
    assert admitted.promotion_performed is False
    assert admitted.admission_integrity_sha256.startswith("sha256:")
    assert admitted.integrity_sha256 != admitted.admission_integrity_sha256


def test_runtime_status_is_sanitized_fail_closed_and_ignores_promotion_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_CODE_ENGINE", "v2")
    monkeypatch.setenv("RAG_CODE_CANARY_PERCENT", "100")
    monkeypatch.setenv("RAG_RELEASE_DECISION", "PROMOTE")
    monkeypatch.setenv("RAG_RELEASE_QUALIFIED", "true")

    status = current_runtime_release_status_v2()
    payload = status.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True)

    assert status.default_engine == "v1"
    assert status.quality_hold is True
    assert status.decision is ReleaseDecision.QUALITY_HOLD
    assert status.release_authority.authority_attested is False
    assert status.release_authority.reviewed_source_count == 0
    assert status.release_authority.reviewed_gate_count == 0
    assert status.source_snapshot.status.value == "UNAVAILABLE"
    assert status.rollback_snapshot.status.value == "UNAVAILABLE"
    assert status.performance_snapshot.status.value == "UNAVAILABLE"
    assert status.verify_only is True
    assert status.sanitized is True
    assert "PROMOTE" not in serialized
    assert "RAG_CODE_ENGINE" not in serialized
    assert "/Users/" not in serialized
    assert "allowed_acl_refs" not in serialized
    assert '"query"' not in serialized
    assert "super-secret" not in serialized


def test_operational_snapshot_is_bounded_sanitized_and_cannot_promote() -> None:
    status = runtime_release_status_from_operational_snapshot_v2(
        {
            "status": "PRODUCTION",
            "default_engine": "v2",
            "quality_qualified": True,
            "query": "secret=super-secret",
            "sources": {
                "registry_version": "production-source-runtime-registry-v2",
                "router_version": "production-source-release-router-v2",
                "release_authority_count": 99,
                "verified_lkg_count": 1,
                "materialized_source_count": 10**100,
                "materialized_sources": ["/Users/private/project"],
                "derived_root_created": True,
                "active_operation_count": -1,
                "project_id": "private-project",
            },
            "component_switches": {
                "version": "token-private-switches",
                "components": [
                    ["planner", "enabled"],
                    ["fusion", "deterministic_baseline"],
                    ["source_retrievers", "v1_fallback"],
                    ["private-query", "enabled"],
                ],
            },
            "reviewed_calibration_count": 2,
            "required_calibration_slice_count": 6,
            "performance": {
                "runtime_version": "bounded-performance-runtime-v2",
                "trace_count": 4,
                "span_count": 8,
                "cache_hits": 3,
                "calibration_availability": "AVAILABLE",
                "unavailable_calibration_slice_count": 0,
                "vector_index": {
                    "index_version": "exact-memory-vector-index-v2",
                    "namespace_count": 1,
                    "generation_count": 1,
                    "active_namespace_count": 1,
                    "vector_count": 10**100,
                    "repository": "/private/repository",
                },
                "embedding_cache": {
                    "cache_version": "content-addressed-embedding-cache-v2",
                    "entry_count": 3,
                },
                "scoped_cache": {
                    "cache_version": "layered-scoped-cache-v2",
                    "entry_count": 2,
                },
                "acl": "team-secret-acl",
            },
        }
    )
    payload = status.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True)

    assert status.default_engine == "v1"
    assert status.quality_hold is True
    assert status.decision is ReleaseDecision.QUALITY_HOLD
    assert status.performance_snapshot.status.value == "UNAVAILABLE"
    assert status.sources.release_authority_count == 99
    assert status.sources.materialized_source_count is None
    assert status.sources.active_operation_count is None
    assert status.sources.code.v2_stage == "NO_RELEASE"
    assert status.sources.code.authority_availability == "UNAVAILABLE"
    assert status.sources.code.last_known_good == "UNKNOWN"
    assert status.component_switches.planner is True
    assert status.component_switches.fusion is False
    assert status.component_switches.source_retrievers is False
    assert status.component_switches.version is None
    assert status.performance.index.vector_count is None
    assert status.performance.latency.availability == "UNAVAILABLE"
    assert status.reviewed_calibration.availability == "AVAILABLE"
    for secret in (
        "super-secret",
        "/Users/private",
        "/private/repository",
        "private-project",
        "team-secret-acl",
        "token-private-switches",
    ):
        assert secret not in serialized


def test_exact_package_verification_separates_integrity_from_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _quality_hold_package(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("verify-only control plane attempted an external side effect")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    verified = verify_exact_release_package_v2(package)
    assert verified.package_kind == "release-admission"
    assert verified.package_integrity_sha256.startswith("sha256:")
    assert verified.integrity_sha256.startswith("sha256:")
    assert verified.authority_attestation_sha256 is None
    assert verified.authority_attested is False
    assert verified.exact_files is True
    assert verified.portable is True
    assert verified.verify_only is True
    assert verified.decision is ReleaseDecision.QUALITY_HOLD


def test_exact_package_copy_passes_but_rebind_tamper_and_caller_self_sign_fail(
    tmp_path: Path,
) -> None:
    package = _quality_hold_package(tmp_path)
    copied = tmp_path / "copied-release-package"
    shutil.copytree(package, copied)
    assert verify_exact_release_package_v2(copied) == verify_exact_release_package_v2(package)

    admission_path = package / "admission.json"
    admission = json.loads(admission_path.read_bytes())
    admission["default_engine"] = "v2"
    admission_path.write_bytes(_canonical_file(admission))
    with pytest.raises(ReleaseAdmissionPackageError, match="checksum"):
        verify_exact_release_package_v2(package)

    forged = {
        "control_plane_version": "rag-release-control-plane-v2",
        "authority_id": "rag-canonical-production-release-authority-v2",
        "package_kind": "release-admission",
        "package_integrity_sha256": "sha256:" + "1" * 64,
        "authority_attestation_sha256": "sha256:" + "2" * 64,
        "authority_attested": True,
        "decision": "PROMOTION_ELIGIBLE",
        "default_engine": "v2",
        "quality_hold": False,
        "exact_files": True,
        "portable": True,
        "verify_only": True,
        "retrieval_executed": False,
        "database_accessed": False,
        "network_accessed": False,
    }
    forged["integrity_sha256"] = canonical_sha256_v2(forged)
    with pytest.raises(ValueError):
        ExactReleasePackageVerificationV2.model_validate(forged)


def test_caller_cannot_admit_plain_or_mutable_self_reported_evidence() -> None:
    plane = canonical_release_control_plane_v2()
    with pytest.raises(ReleaseControlPlaneError, match="frozen source"):
        plane.admit_reviewed_evidence(artifacts=({"quality_qualified": True},))  # type: ignore[arg-type]
