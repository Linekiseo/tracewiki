from __future__ import annotations

import base64
import copy
import json
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evidence_rag.evaluation import maturity_g0_admission_v2 as g0_contract
from evidence_rag.evaluation.maturity_g1_entry_handoff_v1 import (
    EntryHandoffError,
    assemble_entry_packet,
    build_signing_request_bundle,
    prepare_unsigned_draft,
)
from evidence_rag.evaluation.maturity_g1_entry_handoff_v1 import (
    main as handoff_main,
)
from evidence_rag.evaluation.maturity_g1_entry_v1 import (
    ENTRY_ATTESTATION_BUNDLE_SCHEMA_VERSION,
    ENTRY_ATTESTATION_SCHEMA_VERSION,
    ENTRY_KEYSET_SCHEMA_VERSION,
    ENTRY_PACKET_SCHEMA_VERSION,
    MaturityG1EntryError,
    attestation_bundle_content_sha256,
    attestation_receipt_sha256,
    attestation_set_sha256,
    attestation_signing_bytes,
    attestation_subject_sha256,
    canonical_json_bytes_v1,
    compute_entry_requirements,
    domain_sha256_v1,
    keyset_content_sha256,
    main,
    packet_content_sha256,
    verify_entry_packet,
)

VERIFICATION_TIME = "2026-08-06T00:00:00.000000Z"
REVIEWED_AT = "2026-08-05T00:00:00.000000Z"
REQUESTED_AT = "2026-08-05T03:00:00.000000Z"
ISSUED_AT = "2026-08-05T04:00:00.000000Z"
EXPIRES_AT = "2026-09-01T00:00:00.000000Z"
KEY_VALID_FROM = "2026-01-01T00:00:00.000000Z"
KEY_EXPIRES_AT = "2027-01-01T00:00:00.000000Z"
REVISION = "a" * 40
TREE = "b" * 40


@dataclass
class EntryFixture:
    packet_path: Path
    bundle_path: Path
    keyset_path: Path
    g0_path: Path
    trusted_keyset_sha256: str
    packet: dict[str, Any]
    bundle: dict[str, Any]
    receipt_map: dict[str, dict[str, Any]]


def _digest(label: str) -> str:
    return domain_sha256_v1("test-entry-fixture-v1", label)


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_bytes(canonical_json_bytes_v1(payload) + b"\n")


def _private_keys() -> dict[str, tuple[Ed25519PrivateKey, list[str]]]:
    return {
        "architecture-key": (
            Ed25519PrivateKey.from_private_bytes(b"\x01" * 32),
            ["architecture"],
        ),
        "ci-key": (Ed25519PrivateKey.from_private_bytes(b"\x02" * 32), ["ci"]),
        "data-key": (
            Ed25519PrivateKey.from_private_bytes(b"\x03" * 32),
            ["data_owner"],
        ),
        "independent-key": (
            Ed25519PrivateKey.from_private_bytes(b"\x04" * 32),
            ["independent_reviewer"],
        ),
        "owner-key": (
            Ed25519PrivateKey.from_private_bytes(b"\x05" * 32),
            ["owner", "rollback_owner"],
        ),
        "release-key": (
            Ed25519PrivateKey.from_private_bytes(b"\x06" * 32),
            ["release_owner"],
        ),
        "security-key": (
            Ed25519PrivateKey.from_private_bytes(b"\x07" * 32),
            ["security"],
        ),
    }


def _build_keyset(private_keys: dict[str, tuple[Ed25519PrivateKey, list[str]]]) -> dict[str, Any]:
    keys = []
    for key_id, (private_key, roles) in sorted(private_keys.items()):
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        keys.append(
            {
                "key_id": key_id,
                "algorithm": "ed25519",
                "public_key_base64": base64.b64encode(public_bytes).decode("ascii"),
                "roles": sorted(roles),
                "valid_from": KEY_VALID_FROM,
                "expires_at": KEY_EXPIRES_AT,
                "revoked": False,
            }
        )
    payload: dict[str, Any] = {
        "schema_version": ENTRY_KEYSET_SCHEMA_VERSION,
        "keys": keys,
    }
    payload["content_sha256"] = keyset_content_sha256(payload)
    return payload


def _receipt(
    *,
    attestation_id: str,
    kind: str,
    issuer_key_id: str,
    issuer_role: str,
    subject: dict[str, Any],
    private_keys: dict[str, tuple[Ed25519PrivateKey, list[str]]],
    revision: str = REVISION,
    issued_at: str = ISSUED_AT,
    expires_at: str = EXPIRES_AT,
) -> dict[str, Any]:
    unsigned: dict[str, Any] = {
        "schema_version": ENTRY_ATTESTATION_SCHEMA_VERSION,
        "attestation_id": attestation_id,
        "kind": kind,
        "subject_sha256": attestation_subject_sha256(kind, subject),
        "revision": revision,
        "issuer_key_id": issuer_key_id,
        "issuer_role": issuer_role,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "decision": "APPROVED",
        "external_reference_sha256": _digest(f"external:{attestation_id}"),
    }
    signature = private_keys[issuer_key_id][0].sign(attestation_signing_bytes(unsigned))
    return {**unsigned, "signature_base64": base64.b64encode(signature).decode("ascii")}


def _subject_without(value: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in keys}


def _ci_subject(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "g0_admission": packet["g0_admission"],
        "generated_output_boundary": packet["generated_output_boundary"],
        "remote_ci": _subject_without(
            packet["remote_ci"],
            "ci_attestation_id",
            "ci_attestation_sha256",
        ),
        "supply_chain_disposition": packet["supply_chain_disposition"],
    }


def _attach(
    value: dict[str, Any],
    prefix: str,
    receipt: dict[str, Any],
) -> None:
    value[f"{prefix}_attestation_id"] = receipt["attestation_id"]
    value[f"{prefix}_attestation_sha256"] = attestation_receipt_sha256(receipt)


def _build_fixture(tmp_path: Path) -> EntryFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    private_keys = _private_keys()
    keyset = _build_keyset(private_keys)

    g0_files = (
        {
            "byte_length": 11,
            "content_sha256": _digest("g0-backend-file"),
            "git_state": "clean",
            "path": "src/evidence_rag/config.py",
            "role": "runtime_source",
            "scope": "backend",
        },
        {
            "byte_length": 12,
            "content_sha256": _digest("g0-test-file"),
            "git_state": "clean",
            "path": "tests/test_config.py",
            "role": "test_source",
            "scope": "tests",
        },
    )
    g0: dict[str, Any] = {
        "base_head": REVISION,
        "database_policy": {
            "evaluation_sqlite_anchors_hashed_as_opaque_bytes": 0,
            "formal_application_databases_included": False,
            "formal_database_opened": False,
            "sidecars_included": False,
        },
        "default_engine": "v1",
        "excluded_files": [],
        "exclusion_counts": {},
        "files": list(g0_files),
        "findings": [],
        "history_fixture_policy": None,
        "owner_reviewed": False,
        "production_authorized": False,
        "release_blocker_count": 0,
        "remote_ci_observed": False,
        "schema_version": g0_contract.MATURITY_G0_ADMISSION_VERSION,
        "scope_policy_sha256": g0_contract._policy_identity(),
        "scopes": g0_contract._scope_summaries(g0_files),
        "source_snapshot_sha256": g0_contract._source_identity(g0_files),
        "state_counts": {
            "clean": 2,
            "modified": 0,
            "modified_and_staged": 0,
            "staged": 0,
            "untracked": 0,
        },
        "status": "VERSION_SCOPE_REFINED",
    }
    g0["content_sha256"] = g0_contract._canonical_sha256(g0)

    revision: dict[str, Any] = {
        "repository_logical_id": "repository-rag",
        "commit_sha": REVISION,
        "tree_sha": TREE,
        "owner_review_attestation_id": None,
        "owner_review_attestation_sha256": None,
        "reviewed_at": REVIEWED_AT,
        "protected_ref": "refs/heads/main",
    }
    owner_subject = _subject_without(
        revision,
        "owner_review_attestation_id",
        "owner_review_attestation_sha256",
    )
    owner_receipt = _receipt(
        attestation_id="att-owner",
        kind="OWNER_REVIEW",
        issuer_key_id="owner-key",
        issuer_role="owner",
        subject=owner_subject,
        private_keys=private_keys,
    )
    _attach(revision, "owner_review", owner_receipt)

    g0_entry: dict[str, Any] = {
        "manifest_schema_version": g0["schema_version"],
        "manifest_content_sha256": g0["content_sha256"],
        "source_snapshot_sha256": g0["source_snapshot_sha256"],
        "file_count": 2,
        "scope_counts": {"backend": 1, "tests": 1},
        "exact_verified": True,
        "exclusions_approved": True,
        "unapproved_exclusion_addition_count": 0,
        "release_blocker_count": 0,
        "cleanroom_receipt_sha256": _digest("cleanroom-receipt"),
        "cleanroom_release_ready": True,
    }
    generated_boundary: dict[str, Any] = {
        "tracked_generated_path_count": 0,
        "release_blocker_count": 0,
        "git_clean": True,
        "build_reconstruction_passed": True,
        "build_contract_sha256": _digest("build-contract"),
    }
    supply_chain: dict[str, Any] = {
        "lockfile_sha256": _digest("lockfile"),
        "sbom_sha256": _digest("sbom"),
        "audit_tool_version": "npm-11.17.0",
        "finding_set_sha256": _digest("empty-findings"),
        "unaccepted_critical_count": 0,
        "unaccepted_high_count": 0,
        "exceptions": [],
    }

    remote: dict[str, Any] = {
        "provider": "github-actions",
        "repository_logical_id": "repository-rag",
        "workflow_logical_id": "g0-source-admission",
        "workflow_source_sha256": _digest("workflow"),
        "run_id": "run-100",
        "run_attempt": 1,
        "trigger": "pull-request",
        "protected_ref": "refs/heads/main",
        "revision": REVISION,
        "source_snapshot_sha256": g0["source_snapshot_sha256"],
        "started_at": "2026-08-05T01:00:00.000000Z",
        "completed_at": "2026-08-05T02:00:00.000000Z",
        "conclusion": "success",
        "jobs": [
            {
                "job_id": "admission",
                "purpose": "admission",
                "python_version": None,
                "conclusion": "success",
                "stages": [
                    "exact-verify",
                    "cleanroom",
                    "supply-chain-audit-sbom",
                    "frontend-test",
                    "frontend-typecheck",
                    "frontend-build",
                    "release-ready",
                ],
                "command_set_sha256": _digest("admission-command"),
                "log_sha256": _digest("admission-log"),
                "artifact_set_sha256": _digest("admission-artifact"),
            },
            {
                "job_id": "py312",
                "purpose": "backend-full-py312",
                "python_version": "3.12",
                "conclusion": "success",
                "stages": ["backend-full"],
                "command_set_sha256": _digest("py312-command"),
                "log_sha256": _digest("py312-log"),
                "artifact_set_sha256": _digest("py312-artifact"),
            },
            {
                "job_id": "py313",
                "purpose": "backend-full-py313",
                "python_version": "3.13",
                "conclusion": "success",
                "stages": ["backend-full"],
                "command_set_sha256": _digest("py313-command"),
                "log_sha256": _digest("py313-log"),
                "artifact_set_sha256": _digest("py313-artifact"),
            },
        ],
        "attempt_history": [
            {
                "run_attempt": 1,
                "conclusion": "success",
                "started_at": "2026-08-05T01:00:00.000000Z",
                "completed_at": "2026-08-05T02:00:00.000000Z",
                "log_sha256": _digest("attempt-1-log"),
                "artifact_set_sha256": _digest("attempt-1-artifact"),
            }
        ],
        "ci_attestation_id": None,
        "ci_attestation_sha256": None,
    }
    ci_subject = {
        "g0_admission": g0_entry,
        "generated_output_boundary": generated_boundary,
        "remote_ci": _subject_without(remote, "ci_attestation_id", "ci_attestation_sha256"),
        "supply_chain_disposition": supply_chain,
    }
    ci_receipt = _receipt(
        attestation_id="att-ci",
        kind="REMOTE_CI",
        issuer_key_id="ci-key",
        issuer_role="ci",
        subject=ci_subject,
        private_keys=private_keys,
    )
    _attach(remote, "ci", ci_receipt)

    decisions: list[dict[str, Any]] = []
    receipts = [owner_receipt, ci_receipt]
    for decision_id in ("D1", "D2", "D3", "D4", "D5"):
        decision: dict[str, Any] = {
            "decision_id": decision_id,
            "choice": f"CHOICE_{decision_id}",
            "status": "ACCEPTED",
            "rationale_sha256": _digest(f"rationale:{decision_id}"),
            "constraints": [f"constraint-{decision_id}"],
            "required_tests": [f"test-{decision_id}"],
            "rollback_owner_attestation_id": None,
            "rollback_owner_attestation_sha256": None,
            "architecture_attestation_id": None,
            "architecture_attestation_sha256": None,
            "effective_revision": REVISION,
            "expires_at": None,
        }
        decision_subject = _subject_without(
            decision,
            "architecture_attestation_id",
            "architecture_attestation_sha256",
            "rollback_owner_attestation_id",
            "rollback_owner_attestation_sha256",
        )
        architecture_receipt = _receipt(
            attestation_id=f"att-architecture-{decision_id.lower()}",
            kind="ARCHITECTURE_DECISION",
            issuer_key_id="architecture-key",
            issuer_role="architecture",
            subject=decision_subject,
            private_keys=private_keys,
        )
        rollback_receipt = _receipt(
            attestation_id=f"att-rollback-{decision_id.lower()}",
            kind="ROLLBACK_OWNERSHIP",
            issuer_key_id="owner-key",
            issuer_role="rollback_owner",
            subject=decision_subject,
            private_keys=private_keys,
        )
        _attach(decision, "architecture", architecture_receipt)
        _attach(decision, "rollback_owner", rollback_receipt)
        decisions.append(decision)
        receipts.extend((architecture_receipt, rollback_receipt))

    data: dict[str, Any] = {
        "mode": "fixture",
        "dataset_logical_id": "fixture-raw-v2",
        "snapshot_sha256": _digest("fixture-snapshot"),
        "schema_sha256": _digest("fixture-schema"),
        "watermark": "fixture-v1",
        "stable_input": True,
        "owner_attestation_id": None,
        "owner_attestation_sha256": None,
        "allowed_operations": ["read-metadata", "read-schema"],
        "forbidden_operations": [
            "connect-production",
            "export-acl",
            "export-path",
            "export-payload",
            "export-row",
            "export-uri",
            "write-v1",
        ],
        "retention": "ephemeral",
        "expires_at": EXPIRES_AT,
    }
    data_subject = _subject_without(data, "owner_attestation_id", "owner_attestation_sha256")
    data_receipt = _receipt(
        attestation_id="att-data",
        kind="DATA_AUTHORIZATION",
        issuer_key_id="data-key",
        issuer_role="data_owner",
        subject=data_subject,
        private_keys=private_keys,
    )
    _attach(data, "owner", data_receipt)
    receipts.append(data_receipt)

    independent: dict[str, Any] = {
        "reviewed_revision": REVISION,
        "scope_tasks": ["T1.1.1:C1-C10"],
        "implementation_paths": [],
        "review_attestation_id": None,
        "review_attestation_sha256": None,
        "reviewed_at": REVIEWED_AT,
    }
    independent_subject = _subject_without(
        independent,
        "review_attestation_id",
        "review_attestation_sha256",
    )
    independent_receipt = _receipt(
        attestation_id="att-independent",
        kind="INDEPENDENT_REVIEW",
        issuer_key_id="independent-key",
        issuer_role="independent_reviewer",
        subject=independent_subject,
        private_keys=private_keys,
    )
    _attach(independent, "review", independent_receipt)
    receipts.append(independent_receipt)

    runtime: dict[str, Any] = {
        "default_engine": "v1",
        "production_database_accessed": False,
        "release_authorized": False,
        "default_pointer_changed": False,
        "formal_schema_mutated": False,
        "config_snapshot_sha256": _digest("config"),
        "release_registry_sha256": _digest("release-registry"),
        "boundary_attestation_id": None,
        "boundary_attestation_sha256": None,
    }
    runtime_subject = _subject_without(
        runtime,
        "boundary_attestation_id",
        "boundary_attestation_sha256",
    )
    runtime_receipt = _receipt(
        attestation_id="att-runtime",
        kind="RUNTIME_BOUNDARY",
        issuer_key_id="release-key",
        issuer_role="release_owner",
        subject=runtime_subject,
        private_keys=private_keys,
    )
    _attach(runtime, "boundary", runtime_receipt)
    receipts.append(runtime_receipt)

    receipts.sort(key=lambda item: item["attestation_id"])
    receipt_map = {
        receipt["attestation_id"]: {
            **receipt,
            "receipt_sha256": attestation_receipt_sha256(receipt),
        }
        for receipt in receipts
    }
    bundle: dict[str, Any] = {
        "schema_version": ENTRY_ATTESTATION_BUNDLE_SCHEMA_VERSION,
        "attestations": receipts,
    }
    bundle["content_sha256"] = attestation_bundle_content_sha256(bundle)

    packet: dict[str, Any] = {
        "schema_version": ENTRY_PACKET_SCHEMA_VERSION,
        "artifact_version": "1",
        "status": "APPROVED",
        "reviewed_revision": revision,
        "g0_admission": g0_entry,
        "remote_ci": remote,
        "generated_output_boundary": generated_boundary,
        "supply_chain_disposition": supply_chain,
        "architecture_decisions": decisions,
        "data_authorization": data,
        "independent_review": independent,
        "runtime_boundary": runtime,
        "requirements": [],
        "limitations": [],
        "blockers": [],
        "attestation_set_sha256": attestation_set_sha256(receipts),
    }
    packet["requirements"] = compute_entry_requirements(
        packet,
        receipt_map,
        verification_time=datetime_from_canonical(VERIFICATION_TIME),
    )
    packet["content_sha256"] = packet_content_sha256(packet)

    packet_path = tmp_path / "entry.json"
    bundle_path = tmp_path / "attestations.json"
    keyset_path = tmp_path / "keyset.json"
    g0_path = tmp_path / "g0.json"
    _write(packet_path, packet)
    _write(bundle_path, bundle)
    _write(keyset_path, keyset)
    _write(g0_path, g0)
    return EntryFixture(
        packet_path=packet_path,
        bundle_path=bundle_path,
        keyset_path=keyset_path,
        g0_path=g0_path,
        trusted_keyset_sha256=keyset["content_sha256"],
        packet=packet,
        bundle=bundle,
        receipt_map=receipt_map,
    )


def datetime_from_canonical(value: str):
    from datetime import UTC, datetime

    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _verify(fixture: EntryFixture, **overrides: Any) -> dict[str, Any]:
    arguments = {
        "verification_time": VERIFICATION_TIME,
        "attestation_bundle_path": fixture.bundle_path,
        "keyset_path": fixture.keyset_path,
        "trusted_keyset_sha256": fixture.trusted_keyset_sha256,
        "g0_admission_path": fixture.g0_path,
        **overrides,
    }
    return verify_entry_packet(fixture.packet_path, **arguments)


def _refresh_packet(fixture: EntryFixture, packet: dict[str, Any]) -> None:
    packet["requirements"] = compute_entry_requirements(
        packet,
        fixture.receipt_map,
        verification_time=datetime_from_canonical(VERIFICATION_TIME),
    )
    packet.pop("content_sha256", None)
    packet["content_sha256"] = packet_content_sha256(packet)
    _write(fixture.packet_path, packet)


def _replace_or_add_receipt(
    fixture: EntryFixture,
    receipt: dict[str, Any],
) -> None:
    receipt_id = receipt["attestation_id"]
    receipts = [
        row
        for row in fixture.bundle["attestations"]
        if row["attestation_id"] != receipt_id
    ]
    receipts.append(receipt)
    receipts.sort(key=lambda row: row["attestation_id"])
    bundle = {
        "schema_version": ENTRY_ATTESTATION_BUNDLE_SCHEMA_VERSION,
        "attestations": receipts,
    }
    bundle["content_sha256"] = attestation_bundle_content_sha256(bundle)
    fixture.bundle = bundle
    fixture.receipt_map[receipt_id] = {
        **receipt,
        "receipt_sha256": attestation_receipt_sha256(receipt),
    }
    _write(fixture.bundle_path, bundle)


def _resign_ci(
    fixture: EntryFixture,
    packet: dict[str, Any],
    *,
    revision: str = REVISION,
) -> None:
    receipt = _receipt(
        attestation_id="att-ci",
        kind="REMOTE_CI",
        issuer_key_id="ci-key",
        issuer_role="ci",
        subject=_ci_subject(packet),
        private_keys=_private_keys(),
        revision=revision,
    )
    _replace_or_add_receipt(fixture, receipt)
    _attach(packet["remote_ci"], "ci", receipt)
    packet["attestation_set_sha256"] = attestation_set_sha256(
        fixture.bundle["attestations"]
    )


def test_approved_entry_requires_all_ten_signed_requirements_and_is_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("network"))
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: pytest.fail("database"))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("subprocess"))

    result = _verify(fixture)

    assert result["status"] == "ENTRY_QUALIFIED"
    assert [row["status"] for row in result["requirements"]] == ["PASS"] * 10
    assert result["g0_manifest_content_sha256"] == fixture.packet["g0_admission"][
        "manifest_content_sha256"
    ]
    assert result["network_accessed"] is False
    assert result["database_accessed"] is False
    assert result["production_database_accessed"] is False
    assert result["release_authorized"] is False


def test_cli_emits_canonical_verification_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _build_fixture(tmp_path)

    assert main(
        [
            str(fixture.packet_path),
            "--at",
            VERIFICATION_TIME,
            "--attestations",
            str(fixture.bundle_path),
            "--keyset",
            str(fixture.keyset_path),
            "--trusted-keyset-sha256",
            fixture.trusted_keyset_sha256,
            "--g0-admission",
            str(fixture.g0_path),
        ]
    ) == 0

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert output.encode("utf-8") == canonical_json_bytes_v1(payload) + b"\n"
    assert payload["status"] == "ENTRY_QUALIFIED"


def test_repository_self_approval_without_external_receipts_fails_closed(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(MaturityG1EntryError, match="reference sets"):
        verify_entry_packet(
            fixture.packet_path,
            verification_time=VERIFICATION_TIME,
            g0_admission_path=fixture.g0_path,
        )


def test_keyset_must_match_out_of_band_trust_anchor(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(MaturityG1EntryError, match="trust anchor"):
        _verify(fixture, trusted_keyset_sha256=_digest("attacker-keyset"))


def test_expired_attestation_or_key_fails_closed(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(MaturityG1EntryError, match="expired|effective"):
        _verify(fixture, verification_time="2027-02-01T00:00:00.000000Z")


def test_tampered_signature_fails_even_when_bundle_digest_is_recomputed(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    bundle = copy.deepcopy(fixture.bundle)
    bundle["attestations"][0]["signature_base64"] = base64.b64encode(b"\x00" * 64).decode(
        "ascii"
    )
    bundle.pop("content_sha256")
    bundle["content_sha256"] = attestation_bundle_content_sha256(bundle)
    _write(fixture.bundle_path, bundle)

    with pytest.raises(MaturityG1EntryError, match="signature"):
        _verify(fixture)


def test_cross_revision_ci_cannot_be_spliced_into_an_approved_packet(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["remote_ci"]["revision"] = "c" * 40
    _resign_ci(fixture, packet, revision="c" * 40)
    _refresh_packet(fixture, packet)

    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


def test_rerun_attempt_history_cannot_overwrite_a_failed_attempt(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["remote_ci"]["run_attempt"] = 2
    packet["remote_ci"]["attempt_history"] = [
        {
            **packet["remote_ci"]["attempt_history"][0],
            "run_attempt": 2,
        }
    ]
    packet.pop("content_sha256")
    packet["content_sha256"] = packet_content_sha256(packet)
    _write(fixture.packet_path, packet)

    with pytest.raises(MaturityG1EntryError, match="attempt history"):
        _verify(fixture)


def test_pending_or_rejected_architecture_decision_cannot_pass_entry(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["architecture_decisions"][0]["status"] = "PENDING"
    _refresh_packet(fixture, packet)

    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


def test_same_key_cannot_approve_owner_and_independent_review(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    keyset = copy.deepcopy(_build_keyset(_private_keys()))
    owner_key = next(row for row in keyset["keys"] if row["key_id"] == "owner-key")
    owner_key["roles"] = ["independent_reviewer", "owner", "rollback_owner"]
    keyset.pop("content_sha256")
    keyset["content_sha256"] = keyset_content_sha256(keyset)
    fixture.trusted_keyset_sha256 = keyset["content_sha256"]
    _write(fixture.keyset_path, keyset)

    packet = copy.deepcopy(fixture.packet)
    subject = _subject_without(
        packet["independent_review"],
        "review_attestation_id",
        "review_attestation_sha256",
    )
    receipt = _receipt(
        attestation_id="att-independent",
        kind="INDEPENDENT_REVIEW",
        issuer_key_id="owner-key",
        issuer_role="independent_reviewer",
        subject=subject,
        private_keys=_private_keys(),
    )
    _replace_or_add_receipt(fixture, receipt)
    _attach(packet["independent_review"], "review", receipt)
    packet["attestation_set_sha256"] = attestation_set_sha256(
        fixture.bundle["attestations"]
    )
    _refresh_packet(fixture, packet)

    assert packet["requirements"][8]["status"] == "HOLD"
    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


def test_g0_file_and_scope_counts_are_recomputed_from_manifest_rows(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["g0_admission"]["file_count"] = 3
    packet["g0_admission"]["scope_counts"] = {"backend": 2, "tests": 1}
    _refresh_packet(fixture, packet)

    with pytest.raises(MaturityG1EntryError, match="recomputed"):
        _verify(fixture)


def test_g0_manifest_must_pass_its_native_full_schema_verifier(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    g0 = json.loads(fixture.g0_path.read_text(encoding="utf-8"))
    g0["scope_policy_sha256"] = _digest("forged-policy")
    g0.pop("content_sha256")
    g0["content_sha256"] = g0_contract._canonical_sha256(g0)
    _write(fixture.g0_path, g0)

    packet = copy.deepcopy(fixture.packet)
    packet["g0_admission"]["manifest_content_sha256"] = g0["content_sha256"]
    _resign_ci(fixture, packet)
    _refresh_packet(fixture, packet)

    with pytest.raises(MaturityG1EntryError, match="native verification"):
        _verify(fixture)


@pytest.mark.parametrize("section", ("g0", "generated", "supply"))
def test_ci_attestation_binds_every_gate_aggregate(tmp_path: Path, section: str) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    if section == "g0":
        packet["g0_admission"]["cleanroom_receipt_sha256"] = _digest("forged-cleanroom")
    elif section == "generated":
        packet["generated_output_boundary"]["build_contract_sha256"] = _digest(
            "forged-build-contract"
        )
    else:
        packet["supply_chain_disposition"]["finding_set_sha256"] = _digest(
            "forged-findings"
        )
    _refresh_packet(fixture, packet)

    assert packet["requirements"][2]["status"] == "HOLD"
    assert packet["requirements"][3]["status"] == "HOLD"
    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


def test_signed_snapshot_mismatch_cannot_pass_entry(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["remote_ci"]["source_snapshot_sha256"] = _digest("different-source")
    _resign_ci(fixture, packet)
    _refresh_packet(fixture, packet)

    assert packet["requirements"][1]["status"] == "HOLD"
    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


def test_signed_generated_output_blocker_cannot_pass_entry(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["generated_output_boundary"]["tracked_generated_path_count"] = 1
    _resign_ci(fixture, packet)
    _refresh_packet(fixture, packet)

    assert packet["requirements"][4]["status"] == "HOLD"
    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


def test_signed_runtime_boundary_change_cannot_pass_entry(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["runtime_boundary"]["default_engine"] = "v2"
    subject = _subject_without(
        packet["runtime_boundary"],
        "boundary_attestation_id",
        "boundary_attestation_sha256",
    )
    receipt = _receipt(
        attestation_id="att-runtime",
        kind="RUNTIME_BOUNDARY",
        issuer_key_id="release-key",
        issuer_role="release_owner",
        subject=subject,
        private_keys=_private_keys(),
    )
    _replace_or_add_receipt(fixture, receipt)
    _attach(packet["runtime_boundary"], "boundary", receipt)
    packet["attestation_set_sha256"] = attestation_set_sha256(
        fixture.bundle["attestations"]
    )
    _refresh_packet(fixture, packet)

    assert packet["requirements"][9]["status"] == "HOLD"
    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


def test_ci_job_requires_content_digests_not_badges_or_log_links(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["remote_ci"]["jobs"][0]["log_sha256"] = "https://ci.test/badge.svg"
    packet.pop("content_sha256")
    packet["content_sha256"] = packet_content_sha256(packet)
    _write(fixture.packet_path, packet)

    with pytest.raises(MaturityG1EntryError, match="canonical SHA-256"):
        _verify(fixture)


def test_supply_exception_must_expire_and_forbid_every_release_stage(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    incomplete_exception = {
        "finding_id": "CVE-fixture",
        "scope": "fixture-only",
        "mitigation": "isolated",
        "owner_attestation_id": None,
        "owner_attestation_sha256": None,
        "expires_at": EXPIRES_AT,
        "closure_condition": "upgrade",
        "forbidden_release_stages": ["default-v2", "production", "shadow"],
    }
    packet["supply_chain_disposition"]["exceptions"] = [incomplete_exception]
    packet.pop("content_sha256")
    packet["content_sha256"] = packet_content_sha256(packet)
    _write(fixture.packet_path, packet)
    with pytest.raises(MaturityG1EntryError, match="forbid every release stage"):
        _verify(fixture)

    fixture = _build_fixture(tmp_path / "expired")
    packet = copy.deepcopy(fixture.packet)
    expired_exception = {
        **incomplete_exception,
        "forbidden_release_stages": ["canary", "default-v2", "production", "shadow"],
        "expires_at": "2026-08-05T23:59:59.000000Z",
    }
    subject = _subject_without(
        expired_exception,
        "owner_attestation_id",
        "owner_attestation_sha256",
    )
    receipt = _receipt(
        attestation_id="att-security-cve-fixture",
        kind="SECURITY_EXCEPTION",
        issuer_key_id="security-key",
        issuer_role="security",
        subject=subject,
        private_keys=_private_keys(),
    )
    _attach(expired_exception, "owner", receipt)
    _replace_or_add_receipt(fixture, receipt)
    packet["supply_chain_disposition"]["exceptions"] = [expired_exception]
    _resign_ci(fixture, packet)
    _refresh_packet(fixture, packet)

    assert packet["requirements"][5]["status"] == "HOLD"
    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


def test_production_authorization_self_claim_cannot_unlock_entry(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["data_authorization"]["mode"] = "authorized-production"
    subject = _subject_without(
        packet["data_authorization"],
        "owner_attestation_id",
        "owner_attestation_sha256",
    )
    receipt = _receipt(
        attestation_id="att-data",
        kind="DATA_AUTHORIZATION",
        issuer_key_id="data-key",
        issuer_role="data_owner",
        subject=subject,
        private_keys=_private_keys(),
    )
    _replace_or_add_receipt(fixture, receipt)
    _attach(packet["data_authorization"], "owner", receipt)
    packet["attestation_set_sha256"] = attestation_set_sha256(
        fixture.bundle["attestations"]
    )
    _refresh_packet(fixture, packet)

    assert packet["requirements"][7]["status"] == "HOLD"
    with pytest.raises(MaturityG1EntryError, match="status"):
        _verify(fixture)


@pytest.mark.parametrize(
    "unsafe",
    (
        "/Users/private/entry.json",
        "https://example.test/approval",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "password=abcdefghijklmnop",
    ),
)
def test_packet_rejects_paths_uris_and_secrets(tmp_path: Path, unsafe: str) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    packet["limitations"] = [unsafe]
    packet.pop("content_sha256")
    packet["content_sha256"] = packet_content_sha256(packet)
    _write(fixture.packet_path, packet)

    with pytest.raises(MaturityG1EntryError, match="path, URI, or secret"):
        _verify(fixture)


def test_duplicate_key_float_and_copy_tamper_are_rejected(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    original = fixture.packet_path.read_text(encoding="utf-8")
    duplicate = original.replace(
        '"artifact_version":"1",',
        '"artifact_version":"1","artifact_version":"1",',
        1,
    )
    fixture.packet_path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(MaturityG1EntryError, match="duplicate"):
        _verify(fixture)

    with pytest.raises(MaturityG1EntryError, match="binary float"):
        canonical_json_bytes_v1({"not_allowed": 1.5})

    fixture = _build_fixture(tmp_path / "copy")
    copied = tmp_path / "copied-entry.json"
    copied.write_bytes(fixture.packet_path.read_bytes())
    copied_payload = copy.deepcopy(fixture.packet)
    copied_payload["limitations"] = ["tampered-copy"]
    _write(copied, copied_payload)
    with pytest.raises(MaturityG1EntryError, match="content digest"):
        verify_entry_packet(
            copied,
            verification_time=VERIFICATION_TIME,
            attestation_bundle_path=fixture.bundle_path,
            keyset_path=fixture.keyset_path,
            trusted_keyset_sha256=fixture.trusted_keyset_sha256,
            g0_admission_path=fixture.g0_path,
        )


def test_nfc_colliding_json_keys_are_rejected_before_schema_validation(tmp_path: Path) -> None:
    packet_path = tmp_path / "nfc-collision.json"
    packet_path.write_bytes(b'{"\xc3\xa9":1,"e\\u0301":2}\n')

    with pytest.raises(MaturityG1EntryError, match="NFC-colliding"):
        verify_entry_packet(packet_path, verification_time=VERIFICATION_TIME)


def test_handoff_draft_and_signing_requests_are_deterministic_and_signature_free(
    tmp_path: Path,
) -> None:
    fixture = _build_fixture(tmp_path)

    draft = prepare_unsigned_draft(
        fixture.packet,
        verification_time=VERIFICATION_TIME,
    )
    requests = build_signing_request_bundle(
        draft,
        requested_at=REQUESTED_AT,
        g0_admission_path=fixture.g0_path,
    )

    assert draft["status"] == "PENDING"
    assert draft["attestation_set_sha256"] == attestation_set_sha256([])
    assert draft["blockers"]
    assert len(requests["requests"]) == 15
    assert [row["request_id"] for row in requests["requests"]] == sorted(
        row["request_id"] for row in requests["requests"]
    )
    serialized = canonical_json_bytes_v1(requests).decode("utf-8")
    assert "signature_base64" not in serialized
    assert "private_key" not in serialized
    assert requests == build_signing_request_bundle(
        draft,
        requested_at=REQUESTED_AT,
        g0_admission_path=fixture.g0_path,
    )


def test_handoff_assembles_external_receipts_and_original_verifier_accepts_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _build_fixture(tmp_path)
    draft = prepare_unsigned_draft(
        fixture.packet,
        verification_time=VERIFICATION_TIME,
    )
    requests = build_signing_request_bundle(
        draft,
        requested_at=REQUESTED_AT,
        g0_admission_path=fixture.g0_path,
    )
    keyset = json.loads(fixture.keyset_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("network"))
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: pytest.fail("database"))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("subprocess"))

    assembled = assemble_entry_packet(
        draft,
        requests,
        fixture.bundle,
        keyset,
        verification_time=VERIFICATION_TIME,
        trusted_keyset_sha256=fixture.trusted_keyset_sha256,
        g0_admission_path=fixture.g0_path,
    )

    assert assembled["status"] == "APPROVED"
    assert assembled["blockers"] == []
    assert [row["status"] for row in assembled["requirements"]] == ["PASS"] * 10
    _write(fixture.packet_path, assembled)
    result = _verify(fixture)
    assert result["status"] == "ENTRY_QUALIFIED"
    assert result["network_accessed"] is False
    assert result["database_accessed"] is False


def test_handoff_cli_emits_canonical_draft_requests_and_assembled_packet(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = _build_fixture(tmp_path)
    assert handoff_main(
        ["draft", str(fixture.packet_path), "--at", VERIFICATION_TIME]
    ) == 0
    draft_output = capsys.readouterr().out
    draft = json.loads(draft_output)
    assert draft_output.encode("utf-8") == canonical_json_bytes_v1(draft) + b"\n"
    draft_path = tmp_path / "draft.json"
    _write(draft_path, draft)

    assert handoff_main(
        [
            "requests",
            str(draft_path),
            "--at",
            REQUESTED_AT,
            "--g0-admission",
            str(fixture.g0_path),
        ]
    ) == 0
    request_output = capsys.readouterr().out
    requests = json.loads(request_output)
    assert request_output.encode("utf-8") == canonical_json_bytes_v1(requests) + b"\n"
    request_path = tmp_path / "requests.json"
    _write(request_path, requests)

    assert handoff_main(
        [
            "assemble",
            str(draft_path),
            "--requests",
            str(request_path),
            "--attestations",
            str(fixture.bundle_path),
            "--keyset",
            str(fixture.keyset_path),
            "--trusted-keyset-sha256",
            fixture.trusted_keyset_sha256,
            "--at",
            VERIFICATION_TIME,
            "--g0-admission",
            str(fixture.g0_path),
        ]
    ) == 0
    assembled_output = capsys.readouterr().out
    assembled = json.loads(assembled_output)
    assert assembled_output.encode("utf-8") == canonical_json_bytes_v1(assembled) + b"\n"
    assert assembled["status"] == "APPROVED"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("decision", "architecture-decisions-not-ready"),
        ("generated", "generated-output-boundary-not-ready"),
        ("runtime", "runtime-boundary-not-ready"),
    ),
)
def test_handoff_refuses_signing_requests_until_non_signature_facts_are_ready(
    tmp_path: Path,
    mutation: str,
    reason: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    packet = copy.deepcopy(fixture.packet)
    if mutation == "decision":
        packet["architecture_decisions"][0]["status"] = "PENDING"
    elif mutation == "generated":
        packet["generated_output_boundary"]["tracked_generated_path_count"] = 1
    else:
        packet["runtime_boundary"]["default_engine"] = "v2"
    _refresh_packet(fixture, packet)
    draft = prepare_unsigned_draft(packet, verification_time=VERIFICATION_TIME)

    with pytest.raises(EntryHandoffError, match=reason):
        build_signing_request_bundle(
            draft,
            requested_at=REQUESTED_AT,
            g0_admission_path=fixture.g0_path,
        )


def test_handoff_refuses_signed_or_approved_input_as_a_signing_draft(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)

    with pytest.raises(EntryHandoffError, match="PENDING"):
        build_signing_request_bundle(
            fixture.packet,
            requested_at=REQUESTED_AT,
            g0_admission_path=fixture.g0_path,
        )


def test_handoff_request_bundle_tamper_fails_even_with_recomputed_digest(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    draft = prepare_unsigned_draft(fixture.packet, verification_time=VERIFICATION_TIME)
    requests = build_signing_request_bundle(
        draft,
        requested_at=REQUESTED_AT,
        g0_admission_path=fixture.g0_path,
    )
    tampered = copy.deepcopy(requests)
    tampered["requests"][0]["request_id"] = "tampered-request"
    tampered.pop("content_sha256")
    tampered["content_sha256"] = domain_sha256_v1(
        "rag-g1-entry-signing-request-bundle-v1",
        tampered,
    )
    keyset = json.loads(fixture.keyset_path.read_text(encoding="utf-8"))

    with pytest.raises(EntryHandoffError, match="does not match"):
        assemble_entry_packet(
            draft,
            tampered,
            fixture.bundle,
            keyset,
            verification_time=VERIFICATION_TIME,
            trusted_keyset_sha256=fixture.trusted_keyset_sha256,
            g0_admission_path=fixture.g0_path,
        )


def test_handoff_rejects_request_or_receipt_time_replay(tmp_path: Path) -> None:
    fixture = _build_fixture(tmp_path)
    draft = prepare_unsigned_draft(fixture.packet, verification_time=VERIFICATION_TIME)

    with pytest.raises(EntryHandoffError, match="signing-request-predates-facts"):
        build_signing_request_bundle(
            draft,
            requested_at=REVIEWED_AT,
            g0_admission_path=fixture.g0_path,
        )

    late_requests = build_signing_request_bundle(
        draft,
        requested_at=VERIFICATION_TIME,
        g0_admission_path=fixture.g0_path,
    )
    keyset = json.loads(fixture.keyset_path.read_text(encoding="utf-8"))
    with pytest.raises(EntryHandoffError, match="predates its signing request"):
        assemble_entry_packet(
            draft,
            late_requests,
            fixture.bundle,
            keyset,
            verification_time=VERIFICATION_TIME,
            trusted_keyset_sha256=fixture.trusted_keyset_sha256,
            g0_admission_path=fixture.g0_path,
        )


@pytest.mark.parametrize("failure", ("wrong-trust", "missing-receipt", "extra-receipt"))
def test_handoff_refuses_untrusted_or_incomplete_external_receipts(
    tmp_path: Path,
    failure: str,
) -> None:
    fixture = _build_fixture(tmp_path)
    draft = prepare_unsigned_draft(fixture.packet, verification_time=VERIFICATION_TIME)
    requests = build_signing_request_bundle(
        draft,
        requested_at=REQUESTED_AT,
        g0_admission_path=fixture.g0_path,
    )
    keyset = json.loads(fixture.keyset_path.read_text(encoding="utf-8"))
    bundle = copy.deepcopy(fixture.bundle)
    trust_digest = fixture.trusted_keyset_sha256
    expected_error = "trust anchor"
    if failure == "wrong-trust":
        trust_digest = _digest("wrong-handoff-trust")
    elif failure == "missing-receipt":
        bundle["attestations"] = bundle["attestations"][1:]
        bundle.pop("content_sha256")
        bundle["content_sha256"] = attestation_bundle_content_sha256(bundle)
        expected_error = "no matching receipt"
    else:
        bundle["attestations"].append(
            _receipt(
                attestation_id="att-ci-extra",
                kind="REMOTE_CI",
                issuer_key_id="ci-key",
                issuer_role="ci",
                subject=_ci_subject(draft),
                private_keys=_private_keys(),
            )
        )
        bundle["attestations"].sort(key=lambda row: row["attestation_id"])
        bundle.pop("content_sha256")
        bundle["content_sha256"] = attestation_bundle_content_sha256(bundle)
        expected_error = "multiple receipts"

    with pytest.raises(EntryHandoffError, match=expected_error):
        assemble_entry_packet(
            draft,
            requests,
            bundle,
            keyset,
            verification_time=VERIFICATION_TIME,
            trusted_keyset_sha256=trust_digest,
            g0_admission_path=fixture.g0_path,
        )


def test_makefile_exposes_the_complete_entry_handoff_sequence() -> None:
    makefile = (Path(__file__).parents[1] / "Makefile").read_text(encoding="utf-8")

    for target in (
        "g1-entry-draft:",
        "g1-entry-signing-requests:",
        "g1-entry-assemble:",
        "g1-entry-verify:",
    ):
        assert target in makefile
    assert makefile.count(
        "python -m evidence_rag.evaluation.maturity_g1_entry_handoff_v1"
    ) == 3
    assert makefile.count('--g0-admission "$(G0_ADMISSION_MANIFEST)"') == 3
