"""Offline handoff workflow for G1 Entry signing requests and assembly.

This module never generates keys or signatures.  It can detach attestations to
produce a PENDING draft, emit deterministic signing requests after non-signature
facts pass, and assemble externally signed receipts under an out-of-band trust
anchor.
"""

from __future__ import annotations

import argparse
import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .maturity_g1_entry_v1 import (
    _ADMISSION_STAGES,
    _FORBIDDEN_RELEASE_STAGES,
    _read_canonical_json,
    _referenced_attestations,
    _remote_ci_subject,
    _subject_without,
    _time,
    _validate_keyset,
    _validate_packet,
    _validate_receipts,
    _verify_g0_manifest,
    attestation_set_sha256,
    attestation_subject_sha256,
    canonical_json_bytes_v1,
    compute_entry_requirements,
    domain_sha256_v1,
    packet_content_sha256,
)
from .maturity_g1_entry_v1 import MaturityG1EntryError as EntryHandoffError

ENTRY_SIGNING_REQUEST_SCHEMA_VERSION = "rag-g1-entry-signing-request-v1"
ENTRY_SIGNING_REQUEST_BUNDLE_SCHEMA_VERSION = "rag-g1-entry-signing-request-bundle-v1"


@dataclass(frozen=True)
class _SigningTarget:
    request_id: str
    kind: str
    issuer_role: str
    revision: str
    subject: dict[str, Any]
    holder: dict[str, Any]
    prefix: str


def _clear_reference(holder: dict[str, Any], prefix: str) -> None:
    holder[f"{prefix}_attestation_id"] = None
    holder[f"{prefix}_attestation_sha256"] = None


def _attach_reference(
    holder: dict[str, Any],
    prefix: str,
    receipt: Mapping[str, Any],
) -> None:
    holder[f"{prefix}_attestation_id"] = receipt["attestation_id"]
    holder[f"{prefix}_attestation_sha256"] = receipt["receipt_sha256"]


def _finalize_packet_content(packet: dict[str, Any]) -> dict[str, Any]:
    packet.pop("content_sha256", None)
    packet["content_sha256"] = packet_content_sha256(packet)
    return _validate_packet(packet)


def prepare_unsigned_draft(
    packet: Mapping[str, Any],
    *,
    verification_time: str,
) -> dict[str, Any]:
    """Return a canonical-valid PENDING draft with every receipt detached."""

    draft = copy.deepcopy(_validate_packet(dict(packet)))
    verified_at = _time(verification_time, "verification time")
    _clear_reference(draft["reviewed_revision"], "owner_review")
    if draft["remote_ci"] is not None:
        _clear_reference(draft["remote_ci"], "ci")
    for exception in draft["supply_chain_disposition"]["exceptions"]:
        _clear_reference(exception, "owner")
    for decision in draft["architecture_decisions"]:
        _clear_reference(decision, "architecture")
        _clear_reference(decision, "rollback_owner")
    _clear_reference(draft["data_authorization"], "owner")
    _clear_reference(draft["independent_review"], "review")
    _clear_reference(draft["runtime_boundary"], "boundary")
    draft["attestation_set_sha256"] = attestation_set_sha256([])
    draft["requirements"] = compute_entry_requirements(
        draft,
        {},
        verification_time=verified_at,
    )
    draft["status"] = "PENDING"
    draft["blockers"] = sorted(
        {row["reason"] for row in draft["requirements"] if row["status"] == "HOLD"}
    )
    return _finalize_packet_content(draft)


def _signing_targets(packet: dict[str, Any]) -> list[_SigningTarget]:
    revision = packet["reviewed_revision"]["commit_sha"]
    targets = [
        _SigningTarget(
            request_id="owner-review",
            kind="OWNER_REVIEW",
            issuer_role="owner",
            revision=revision,
            subject=_subject_without(
                packet["reviewed_revision"],
                "owner_review_attestation_id",
                "owner_review_attestation_sha256",
            ),
            holder=packet["reviewed_revision"],
            prefix="owner_review",
        )
    ]
    remote = packet["remote_ci"]
    if remote is not None:
        targets.append(
            _SigningTarget(
                request_id="remote-ci",
                kind="REMOTE_CI",
                issuer_role="ci",
                revision=revision,
                subject=_remote_ci_subject(packet),
                holder=remote,
                prefix="ci",
            )
        )
    for exception in packet["supply_chain_disposition"]["exceptions"]:
        targets.append(
            _SigningTarget(
                request_id=f"security-{exception['finding_id']}",
                kind="SECURITY_EXCEPTION",
                issuer_role="security",
                revision=revision,
                subject=_subject_without(
                    exception,
                    "owner_attestation_id",
                    "owner_attestation_sha256",
                ),
                holder=exception,
                prefix="owner",
            )
        )
    for decision in packet["architecture_decisions"]:
        subject = _subject_without(
            decision,
            "architecture_attestation_id",
            "architecture_attestation_sha256",
            "rollback_owner_attestation_id",
            "rollback_owner_attestation_sha256",
        )
        decision_id = decision["decision_id"].lower()
        targets.extend(
            (
                _SigningTarget(
                    request_id=f"architecture-{decision_id}",
                    kind="ARCHITECTURE_DECISION",
                    issuer_role="architecture",
                    revision=revision,
                    subject=subject,
                    holder=decision,
                    prefix="architecture",
                ),
                _SigningTarget(
                    request_id=f"rollback-{decision_id}",
                    kind="ROLLBACK_OWNERSHIP",
                    issuer_role="rollback_owner",
                    revision=revision,
                    subject=subject,
                    holder=decision,
                    prefix="rollback_owner",
                ),
            )
        )
    data = packet["data_authorization"]
    targets.append(
        _SigningTarget(
            request_id="data-authorization",
            kind="DATA_AUTHORIZATION",
            issuer_role="data_owner",
            revision=revision,
            subject=_subject_without(
                data,
                "owner_attestation_id",
                "owner_attestation_sha256",
            ),
            holder=data,
            prefix="owner",
        )
    )
    independent = packet["independent_review"]
    targets.append(
        _SigningTarget(
            request_id="independent-review",
            kind="INDEPENDENT_REVIEW",
            issuer_role="independent_reviewer",
            revision=revision,
            subject=_subject_without(
                independent,
                "review_attestation_id",
                "review_attestation_sha256",
            ),
            holder=independent,
            prefix="review",
        )
    )
    runtime = packet["runtime_boundary"]
    targets.append(
        _SigningTarget(
            request_id="runtime-boundary",
            kind="RUNTIME_BOUNDARY",
            issuer_role="release_owner",
            revision=revision,
            subject=_subject_without(
                runtime,
                "boundary_attestation_id",
                "boundary_attestation_sha256",
            ),
            holder=runtime,
            prefix="boundary",
        )
    )
    return sorted(targets, key=lambda target: target.request_id)


def _fact_blockers(packet: Mapping[str, Any], verification_time: datetime) -> list[str]:
    blockers: set[str] = set()
    revision = packet["reviewed_revision"]
    commit_sha = revision["commit_sha"]
    if revision["reviewed_at"] is None:
        blockers.add("owner-review-time-missing")
    g0 = packet["g0_admission"]
    remote = packet["remote_ci"]
    if not (
        g0["exact_verified"]
        and g0["exclusions_approved"]
        and g0["unapproved_exclusion_addition_count"] == 0
        and g0["release_blocker_count"] == 0
        and g0["cleanroom_receipt_sha256"] is not None
        and g0["cleanroom_release_ready"]
    ):
        blockers.add("g0-admission-facts-not-ready")
    jobs_by_purpose: dict[str, Mapping[str, Any]] = {}
    if remote is None:
        blockers.add("remote-ci-missing")
    else:
        jobs_by_purpose = {job["purpose"]: job for job in remote["jobs"]}
        if not (
            remote["revision"] == commit_sha
            and remote["repository_logical_id"] == revision["repository_logical_id"]
            and remote["source_snapshot_sha256"] == g0["source_snapshot_sha256"]
        ):
            blockers.add("remote-ci-identity-mismatch")
    completed_fact_times = [
        _time(revision["reviewed_at"], "owner review time")
        if revision["reviewed_at"] is not None
        else verification_time,
        _time(packet["independent_review"]["reviewed_at"], "independent review time"),
    ]
    if remote is not None:
        completed_fact_times.append(_time(remote["completed_at"], "remote CI completed_at"))
    if any(fact_time > verification_time for fact_time in completed_fact_times):
        blockers.add("signing-request-predates-facts")
    py312 = jobs_by_purpose.get("backend-full-py312")
    py313 = jobs_by_purpose.get("backend-full-py313")
    if not (
        py312
        and py313
        and py312["python_version"] == "3.12"
        and py313["python_version"] == "3.13"
        and py312["stages"] == ["backend-full"]
        and py313["stages"] == ["backend-full"]
    ):
        blockers.add("remote-python-matrix-not-ready")
    admission = jobs_by_purpose.get("admission")
    if not (
        admission
        and admission["python_version"] is None
        and tuple(admission["stages"]) == _ADMISSION_STAGES
    ):
        blockers.add("remote-admission-job-not-ready")
    generated = packet["generated_output_boundary"]
    if not (
        generated["tracked_generated_path_count"] == 0
        and generated["release_blocker_count"] == 0
        and generated["git_clean"]
        and generated["build_reconstruction_passed"]
    ):
        blockers.add("generated-output-boundary-not-ready")
    supply = packet["supply_chain_disposition"]
    if supply["unaccepted_critical_count"] or supply["unaccepted_high_count"]:
        blockers.add("supply-chain-findings-unaccepted")
    for exception in supply["exceptions"]:
        if (
            _time(exception["expires_at"], "supply exception expiry") <= verification_time
            or tuple(exception["forbidden_release_stages"]) != _FORBIDDEN_RELEASE_STAGES
        ):
            blockers.add("supply-chain-exception-not-ready")
    for decision in packet["architecture_decisions"]:
        if not (
            decision["status"] in {"ACCEPTED", "ACCEPTED_WITH_REVIEWED_ADR"}
            and decision["effective_revision"] == commit_sha
            and (
                decision["expires_at"] is None
                or _time(decision["expires_at"], "decision expiry") > verification_time
            )
        ):
            blockers.add("architecture-decisions-not-ready")
    data = packet["data_authorization"]
    if not (
        data["mode"] == "fixture"
        and data["stable_input"]
        and "connect-production" in data["forbidden_operations"]
        and _time(data["expires_at"], "data authorization expiry") > verification_time
    ):
        blockers.add("data-authorization-facts-not-ready")
    independent = packet["independent_review"]
    if not (
        independent["reviewed_revision"] == commit_sha
        and independent["scope_tasks"] == ["T1.1.1:C1-C10"]
        and independent["implementation_paths"] == []
    ):
        blockers.add("independent-review-scope-not-ready")
    runtime = packet["runtime_boundary"]
    if not (
        runtime["default_engine"] == "v1"
        and not runtime["production_database_accessed"]
        and not runtime["release_authorized"]
        and not runtime["default_pointer_changed"]
        and not runtime["formal_schema_mutated"]
    ):
        blockers.add("runtime-boundary-not-ready")
    return sorted(blockers)


def _ensure_unsigned_draft(
    packet: dict[str, Any],
    *,
    verification_time: datetime,
    g0_admission_path: Path,
) -> None:
    if packet["status"] != "PENDING":
        raise EntryHandoffError("signing draft status must be PENDING")
    if _referenced_attestations(packet):
        raise EntryHandoffError("signing draft must not contain attestation references")
    if packet["attestation_set_sha256"] != attestation_set_sha256([]):
        raise EntryHandoffError("signing draft attestation set must be empty")
    expected = compute_entry_requirements(packet, {}, verification_time=verification_time)
    if packet["requirements"] != expected:
        raise EntryHandoffError("signing draft requirements are not recomputed")
    expected_blockers = sorted({row["reason"] for row in expected if row["status"] == "HOLD"})
    if packet["blockers"] != expected_blockers:
        raise EntryHandoffError("signing draft blockers are not recomputed")
    _verify_g0_manifest(g0_admission_path, packet["g0_admission"])
    blockers = _fact_blockers(packet, verification_time)
    if blockers:
        raise EntryHandoffError("facts are not ready for signing: " + ",".join(blockers))


def _request_row(target: _SigningTarget, packet_digest: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema_version": ENTRY_SIGNING_REQUEST_SCHEMA_VERSION,
        "request_id": target.request_id,
        "kind": target.kind,
        "issuer_role": target.issuer_role,
        "revision": target.revision,
        "subject": target.subject,
        "subject_sha256": attestation_subject_sha256(target.kind, target.subject),
        "packet_content_sha256": packet_digest,
    }
    row["content_sha256"] = domain_sha256_v1("rag-g1-entry-signing-request-v1", row)
    return row


def build_signing_request_bundle(
    draft_packet: Mapping[str, Any],
    *,
    requested_at: str,
    g0_admission_path: Path,
) -> dict[str, Any]:
    packet = _validate_packet(copy.deepcopy(dict(draft_packet)))
    requested_time = _time(requested_at, "signing request time")
    _ensure_unsigned_draft(
        packet,
        verification_time=requested_time,
        g0_admission_path=g0_admission_path,
    )
    requests = [_request_row(target, packet["content_sha256"]) for target in _signing_targets(packet)]
    bundle: dict[str, Any] = {
        "schema_version": ENTRY_SIGNING_REQUEST_BUNDLE_SCHEMA_VERSION,
        "requested_at": requested_at,
        "packet_content_sha256": packet["content_sha256"],
        "g0_manifest_content_sha256": packet["g0_admission"]["manifest_content_sha256"],
        "requests": requests,
    }
    bundle["content_sha256"] = domain_sha256_v1(
        "rag-g1-entry-signing-request-bundle-v1",
        bundle,
    )
    return bundle


def _validate_request_bundle(
    bundle: Mapping[str, Any],
    *,
    packet: dict[str, Any],
    g0_admission_path: Path,
) -> None:
    if set(bundle) != {
        "schema_version",
        "requested_at",
        "packet_content_sha256",
        "g0_manifest_content_sha256",
        "requests",
        "content_sha256",
    }:
        raise EntryHandoffError("signing request bundle field contract mismatch")
    if bundle["schema_version"] != ENTRY_SIGNING_REQUEST_BUNDLE_SCHEMA_VERSION:
        raise EntryHandoffError("signing request bundle schema is unsupported")
    unsigned = {key: value for key, value in bundle.items() if key != "content_sha256"}
    if bundle["content_sha256"] != domain_sha256_v1(
        "rag-g1-entry-signing-request-bundle-v1",
        unsigned,
    ):
        raise EntryHandoffError("signing request bundle content digest mismatch")
    expected = build_signing_request_bundle(
        packet,
        requested_at=bundle["requested_at"],
        g0_admission_path=g0_admission_path,
    )
    if bundle != expected:
        raise EntryHandoffError("signing request bundle does not match the draft packet")


def assemble_entry_packet(
    draft_packet: Mapping[str, Any],
    signing_request_bundle: Mapping[str, Any],
    attestation_bundle: Mapping[str, Any],
    trusted_keyset: Mapping[str, Any],
    *,
    verification_time: str,
    trusted_keyset_sha256: str,
    g0_admission_path: Path,
) -> dict[str, Any]:
    packet = _validate_packet(copy.deepcopy(dict(draft_packet)))
    verified_at = _time(verification_time, "verification time")
    _ensure_unsigned_draft(
        packet,
        verification_time=verified_at,
        g0_admission_path=g0_admission_path,
    )
    _validate_request_bundle(
        signing_request_bundle,
        packet=packet,
        g0_admission_path=g0_admission_path,
    )
    keys = _validate_keyset(
        dict(trusted_keyset),
        trusted_keyset_sha256=trusted_keyset_sha256,
        verification_time=verified_at,
    )
    receipts = _validate_receipts(
        dict(attestation_bundle),
        keys=keys,
        verification_time=verified_at,
    )
    requested_at = _time(signing_request_bundle["requested_at"], "signing request time")
    if any(receipt["issued_at_parsed"] < requested_at for receipt in receipts.values()):
        raise EntryHandoffError("attestation receipt predates its signing request")
    targets = _signing_targets(packet)
    receipt_index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for receipt in receipts.values():
        identity = (
            receipt["kind"],
            receipt["issuer_role"],
            receipt["revision"],
            receipt["subject_sha256"],
        )
        if identity in receipt_index:
            raise EntryHandoffError("multiple receipts satisfy one signing request")
        receipt_index[identity] = receipt
    matched_ids: set[str] = set()
    for target in targets:
        identity = (
            target.kind,
            target.issuer_role,
            target.revision,
            attestation_subject_sha256(target.kind, target.subject),
        )
        receipt = receipt_index.get(identity)
        if receipt is None:
            raise EntryHandoffError(f"signing request has no matching receipt: {target.request_id}")
        matched_ids.add(receipt["attestation_id"])
        _attach_reference(target.holder, target.prefix, receipt)
    if matched_ids != set(receipts):
        raise EntryHandoffError("attestation bundle contains receipts outside the signing request set")
    raw_receipts = [
        {
            key: value
            for key, value in receipts[receipt_id].items()
            if not key.endswith("_parsed") and key != "receipt_sha256"
        }
        for receipt_id in sorted(receipts)
    ]
    packet["attestation_set_sha256"] = attestation_set_sha256(raw_receipts)
    packet["requirements"] = compute_entry_requirements(
        packet,
        receipts,
        verification_time=verified_at,
    )
    hold_reasons = sorted(
        {row["reason"] for row in packet["requirements"] if row["status"] == "HOLD"}
    )
    if hold_reasons:
        raise EntryHandoffError(
            "externally signed packet does not satisfy Entry: " + ",".join(hold_reasons)
        )
    packet["status"] = "APPROVED"
    packet["blockers"] = []
    packet = _finalize_packet_content(packet)
    _verify_g0_manifest(g0_admission_path, packet["g0_admission"])
    return packet


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft")
    draft.add_argument("packet", type=Path)
    draft.add_argument("--at", required=True, dest="verification_time")
    requests = commands.add_parser("requests")
    requests.add_argument("draft", type=Path)
    requests.add_argument("--at", required=True, dest="requested_at")
    requests.add_argument("--g0-admission", required=True, type=Path)
    assemble = commands.add_parser("assemble")
    assemble.add_argument("draft", type=Path)
    assemble.add_argument("--requests", required=True, type=Path)
    assemble.add_argument("--attestations", required=True, type=Path)
    assemble.add_argument("--keyset", required=True, type=Path)
    assemble.add_argument("--trusted-keyset-sha256", required=True)
    assemble.add_argument("--at", required=True, dest="verification_time")
    assemble.add_argument("--g0-admission", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "draft":
            packet = _read_canonical_json(arguments.packet, label="entry packet")
            result = prepare_unsigned_draft(
                packet,
                verification_time=arguments.verification_time,
            )
        elif arguments.command == "requests":
            draft = _read_canonical_json(arguments.draft, label="entry draft")
            result = build_signing_request_bundle(
                draft,
                requested_at=arguments.requested_at,
                g0_admission_path=arguments.g0_admission,
            )
        else:
            draft = _read_canonical_json(arguments.draft, label="entry draft")
            request_bundle = _read_canonical_json(
                arguments.requests,
                label="signing request bundle",
            )
            attestations = _read_canonical_json(
                arguments.attestations,
                label="attestation bundle",
            )
            keyset = _read_canonical_json(arguments.keyset, label="trusted keyset")
            result = assemble_entry_packet(
                draft,
                request_bundle,
                attestations,
                keyset,
                verification_time=arguments.verification_time,
                trusted_keyset_sha256=arguments.trusted_keyset_sha256,
                g0_admission_path=arguments.g0_admission,
            )
    except (OSError, EntryHandoffError) as exc:
        parser.error(str(exc))
    print(canonical_json_bytes_v1(result).decode("utf-8"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ENTRY_SIGNING_REQUEST_BUNDLE_SCHEMA_VERSION",
    "ENTRY_SIGNING_REQUEST_SCHEMA_VERSION",
    "EntryHandoffError",
    "assemble_entry_packet",
    "build_signing_request_bundle",
    "prepare_unsigned_draft",
]
