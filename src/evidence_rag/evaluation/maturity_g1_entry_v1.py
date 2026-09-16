"""Offline, fail-closed verification for the G1 E-01..E-10 entry packet.

The verifier performs no Git-provider, network, or database I/O.  External
authority is represented by Ed25519 receipts and an out-of-band trusted keyset
digest supplied by the verifier caller.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .maturity_g0_admission_v1 import MaturityG0AdmissionError
from .maturity_g0_admission_v2 import verify_admission_manifest

ENTRY_PACKET_SCHEMA_VERSION = "rag-g1-entry-decision-v1"
ENTRY_VERIFICATION_SCHEMA_VERSION = "rag-g1-entry-verification-v1"
ENTRY_ATTESTATION_SCHEMA_VERSION = "rag-g1-entry-attestation-v1"
ENTRY_ATTESTATION_BUNDLE_SCHEMA_VERSION = "rag-g1-entry-attestation-bundle-v1"
ENTRY_KEYSET_SCHEMA_VERSION = "rag-g1-entry-keyset-v1"

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_HASH_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,511}$")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s\"'(])/(?:Users|home|private|tmp|var|etc|root|Volumes|opt|srv)(?:/|\b)|"
    r"(?i:(?:^|[\s\"'(])[A-Z]:[\\/]|\\\\[^\\/\s]+[\\/][^\\/\s]+)"
)
_SECRET_VALUE_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?i:(?:api[_-]?key|access[_-]?token|password|cookie|secret|credential)"
    r"\s*[:=]\s*\S{8,})|(?i:Bearer\s+\S{8,})"
)

_ENTRY_IDS = tuple(f"E-{index:02d}" for index in range(1, 11))
_DECISION_IDS = tuple(f"D{index}" for index in range(1, 6))
_DECISION_STATUSES = frozenset(
    {"ACCEPTED", "ACCEPTED_WITH_REVIEWED_ADR", "PENDING", "REJECTED"}
)
_PACKET_STATUSES = frozenset({"PENDING", "APPROVED", "REJECTED", "EXPIRED"})
_ALLOWED_ROLES = frozenset(
    {
        "architecture",
        "ci",
        "data_owner",
        "independent_reviewer",
        "owner",
        "release_owner",
        "rollback_owner",
        "security",
    }
)
_ATTESTATION_ROLE_BY_KIND = {
    "ARCHITECTURE_DECISION": "architecture",
    "DATA_AUTHORIZATION": "data_owner",
    "INDEPENDENT_REVIEW": "independent_reviewer",
    "OWNER_REVIEW": "owner",
    "REMOTE_CI": "ci",
    "ROLLBACK_OWNERSHIP": "rollback_owner",
    "RUNTIME_BOUNDARY": "release_owner",
    "SECURITY_EXCEPTION": "security",
}
_ADMISSION_STAGES = (
    "exact-verify",
    "cleanroom",
    "supply-chain-audit-sbom",
    "frontend-test",
    "frontend-typecheck",
    "frontend-build",
    "release-ready",
)
_FORBIDDEN_RELEASE_STAGES = (
    "canary",
    "default-v2",
    "production",
    "shadow",
)


class MaturityG1EntryError(RuntimeError):
    """The entry packet, receipts, or trust material is invalid."""


def canonical_json_bytes_v1(value: object) -> bytes:
    """Return canonical-json-v1 bytes after strict value-domain validation."""

    normalized = _canonical_value(value, label="canonical value")
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def domain_sha256_v1(domain: str, value: object) -> str:
    if not domain.isascii() or not domain or "\x00" in domain:
        raise MaturityG1EntryError("digest domain is invalid")
    digest = hashlib.sha256(domain.encode("ascii") + b"\x00" + canonical_json_bytes_v1(value))
    return "sha256:" + digest.hexdigest()


def packet_content_sha256(packet_without_digest: Mapping[str, Any]) -> str:
    return domain_sha256_v1("rag-g1-entry-packet-v1", packet_without_digest)


def keyset_content_sha256(keyset_without_digest: Mapping[str, Any]) -> str:
    return domain_sha256_v1("rag-g1-entry-keyset-v1", keyset_without_digest)


def attestation_bundle_content_sha256(bundle_without_digest: Mapping[str, Any]) -> str:
    return domain_sha256_v1("rag-g1-entry-attestation-bundle-v1", bundle_without_digest)


def attestation_receipt_sha256(receipt: Mapping[str, Any]) -> str:
    return domain_sha256_v1("rag-g1-entry-attestation-receipt-v1", receipt)


def attestation_set_sha256(receipts: Sequence[Mapping[str, Any]]) -> str:
    digests = sorted(attestation_receipt_sha256(receipt) for receipt in receipts)
    return domain_sha256_v1("rag-g1-entry-attestation-set-v1", digests)


def attestation_subject_sha256(kind: str, subject: Mapping[str, Any]) -> str:
    return domain_sha256_v1(
        "rag-g1-entry-attestation-subject-v1",
        {"kind": kind, "subject": subject},
    )


def attestation_signing_bytes(receipt_without_signature: Mapping[str, Any]) -> bytes:
    return (
        b"rag-g1-entry-attestation-v1\x00"
        + canonical_json_bytes_v1(receipt_without_signature)
    )


def _canonical_value(value: object, *, label: str) -> object:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        raise MaturityG1EntryError(f"{label} contains a binary float")
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        if normalized != value:
            raise MaturityG1EntryError(f"{label} contains a non-NFC string")
        if any(ord(character) < 32 for character in value):
            raise MaturityG1EntryError(f"{label} contains a control character")
        return value
    if isinstance(value, list):
        return [
            _canonical_value(item, label=f"{label}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MaturityG1EntryError(f"{label} contains a non-string object key")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key != key:
                raise MaturityG1EntryError(f"{label} contains a non-NFC object key")
            if normalized_key in normalized:
                raise MaturityG1EntryError(f"{label} contains a canonical duplicate key")
            normalized[normalized_key] = _canonical_value(
                item,
                label=f"{label}.{normalized_key}",
            )
        return normalized
    raise MaturityG1EntryError(f"{label} contains an unsupported runtime value")


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    normalized_keys: set[str] = set()
    for key, value in pairs:
        if key in result:
            raise MaturityG1EntryError("JSON contains a duplicate object key")
        normalized = unicodedata.normalize("NFC", key)
        if normalized in normalized_keys:
            raise MaturityG1EntryError("JSON contains an NFC-colliding object key")
        normalized_keys.add(normalized)
        result[key] = value
    return result


def _reject_float(_value: str) -> object:
    raise MaturityG1EntryError("JSON contains a binary float")


def _reject_constant(_value: str) -> object:
    raise MaturityG1EntryError("JSON contains a non-finite number")


def _read_canonical_json(path: Path, *, label: str) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_object_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MaturityG1EntryError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise MaturityG1EntryError(f"{label} root must be an object")
    canonical = canonical_json_bytes_v1(payload) + b"\n"
    if raw != canonical:
        raise MaturityG1EntryError(f"{label} is not canonical-json-v1")
    return payload


def _expect_keys(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise MaturityG1EntryError(f"{label} field contract mismatch")
    return value


def _string(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise MaturityG1EntryError(f"{label} must be a non-empty string")
    if value != value.strip() or len(value.encode("utf-8")) > 8_192:
        raise MaturityG1EntryError(f"{label} is not bounded canonical text")
    return value


def _safe_id(value: object, label: str) -> str:
    text = _string(value, label)
    if _SAFE_ID_RE.fullmatch(text) is None:
        raise MaturityG1EntryError(f"{label} is not a portable identifier")
    return text


def _digest(value: object, label: str) -> str:
    text = _string(value, label)
    if _SHA256_RE.fullmatch(text) is None:
        raise MaturityG1EntryError(f"{label} is not a canonical SHA-256")
    return text


def _git_hash(value: object, label: str) -> str:
    text = _string(value, label)
    if _GIT_HASH_RE.fullmatch(text) is None:
        raise MaturityG1EntryError(f"{label} is not a Git object identity")
    return text


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise MaturityG1EntryError(f"{label} is not a valid integer")
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise MaturityG1EntryError(f"{label} is not a boolean")
    return value


def _time(value: object, label: str) -> datetime:
    text = _string(value, label)
    if _TIME_RE.fullmatch(text) is None:
        raise MaturityG1EntryError(f"{label} is not canonical UTC microsecond time")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError as exc:
        raise MaturityG1EntryError(f"{label} is not a valid timestamp") from exc
    return parsed.replace(tzinfo=UTC)


def _optional_time(value: object, label: str) -> datetime | None:
    return None if value is None else _time(value, label)


def _sorted_strings(value: object, label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise MaturityG1EntryError(f"{label} must be a list")
    rows = tuple(_string(item, f"{label} item") for item in value)
    if rows != tuple(sorted(set(rows))) or (not allow_empty and not rows):
        raise MaturityG1EntryError(f"{label} must be sorted and unique")
    return rows


def _ordered_strings(value: object, label: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise MaturityG1EntryError(f"{label} must be a list")
    rows = tuple(_string(item, f"{label} item") for item in value)
    if len(rows) != len(set(rows)) or (not allow_empty and not rows):
        raise MaturityG1EntryError(f"{label} must be unique")
    return rows


def _reference_pair(value: Mapping[str, Any], prefix: str, label: str) -> tuple[str, str] | None:
    attestation_id = value[f"{prefix}_attestation_id"]
    attestation_sha256 = value[f"{prefix}_attestation_sha256"]
    if attestation_id is None and attestation_sha256 is None:
        return None
    if attestation_id is None or attestation_sha256 is None:
        raise MaturityG1EntryError(f"{label} attestation reference is partial")
    return _safe_id(attestation_id, f"{label} attestation id"), _digest(
        attestation_sha256,
        f"{label} attestation digest",
    )


def _security_scan(value: object, *, label: str = "packet") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = key.lower()
            if lowered in {
                "api_key",
                "cookie",
                "credential",
                "password",
                "private_key",
                "secret",
                "token",
            }:
                raise MaturityG1EntryError(f"{label} contains a forbidden security field")
            _security_scan(item, label=f"{label}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _security_scan(item, label=f"{label}[{index}]")
        return
    if isinstance(value, str) and (
        _ABSOLUTE_PATH_RE.search(value)
        or _SECRET_VALUE_RE.search(value)
        or "://" in value
        or value.startswith("//")
    ):
        raise MaturityG1EntryError(f"{label} contains a path, URI, or secret")


def _validate_keyset(
    payload: dict[str, Any],
    *,
    trusted_keyset_sha256: str,
    verification_time: datetime,
) -> dict[str, dict[str, Any]]:
    _expect_keys(payload, {"schema_version", "keys", "content_sha256"}, "keyset")
    if payload["schema_version"] != ENTRY_KEYSET_SCHEMA_VERSION:
        raise MaturityG1EntryError("keyset schema is unsupported")
    _digest(trusted_keyset_sha256, "trusted keyset digest")
    unsigned = {key: value for key, value in payload.items() if key != "content_sha256"}
    expected = keyset_content_sha256(unsigned)
    if payload["content_sha256"] != expected or expected != trusted_keyset_sha256:
        raise MaturityG1EntryError("keyset does not match the out-of-band trust anchor")
    if not isinstance(payload["keys"], list) or not payload["keys"]:
        raise MaturityG1EntryError("trusted keyset is empty")
    keys: dict[str, dict[str, Any]] = {}
    for index, raw_key in enumerate(payload["keys"]):
        key = _expect_keys(
            raw_key,
            {
                "key_id",
                "algorithm",
                "public_key_base64",
                "roles",
                "valid_from",
                "expires_at",
                "revoked",
            },
            f"keyset key {index}",
        )
        key_id = _safe_id(key["key_id"], "key id")
        if key_id in keys:
            raise MaturityG1EntryError("keyset key ids are not unique")
        if key["algorithm"] != "ed25519":
            raise MaturityG1EntryError("keyset algorithm is unsupported")
        roles = _sorted_strings(key["roles"], "key roles", allow_empty=False)
        if not set(roles) <= _ALLOWED_ROLES:
            raise MaturityG1EntryError("keyset contains an unsupported authority role")
        valid_from = _time(key["valid_from"], "key valid_from")
        expires_at = _time(key["expires_at"], "key expires_at")
        if valid_from >= expires_at:
            raise MaturityG1EntryError("key validity interval is empty")
        revoked = _boolean(key["revoked"], "key revoked")
        try:
            public_bytes = base64.b64decode(key["public_key_base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MaturityG1EntryError("keyset public key is not canonical base64") from exc
        if len(public_bytes) != 32:
            raise MaturityG1EntryError("keyset public key length is invalid")
        keys[key_id] = {
            **key,
            "roles": roles,
            "valid_from_parsed": valid_from,
            "expires_at_parsed": expires_at,
            "public_bytes": public_bytes,
            "currently_valid": not revoked and valid_from <= verification_time < expires_at,
        }
    if tuple(keys) != tuple(sorted(keys)):
        raise MaturityG1EntryError("keyset keys must be sorted by key_id")
    return keys


def _validate_receipts(
    payload: dict[str, Any],
    *,
    keys: Mapping[str, Mapping[str, Any]],
    verification_time: datetime,
) -> dict[str, dict[str, Any]]:
    _expect_keys(payload, {"schema_version", "attestations", "content_sha256"}, "bundle")
    if payload["schema_version"] != ENTRY_ATTESTATION_BUNDLE_SCHEMA_VERSION:
        raise MaturityG1EntryError("attestation bundle schema is unsupported")
    unsigned_bundle = {key: value for key, value in payload.items() if key != "content_sha256"}
    if payload["content_sha256"] != attestation_bundle_content_sha256(unsigned_bundle):
        raise MaturityG1EntryError("attestation bundle content digest mismatch")
    raw_receipts = payload["attestations"]
    if not isinstance(raw_receipts, list):
        raise MaturityG1EntryError("attestation bundle rows are missing")
    receipts: dict[str, dict[str, Any]] = {}
    for index, raw_receipt in enumerate(raw_receipts):
        receipt = _expect_keys(
            raw_receipt,
            {
                "schema_version",
                "attestation_id",
                "kind",
                "subject_sha256",
                "revision",
                "issuer_key_id",
                "issuer_role",
                "issued_at",
                "expires_at",
                "decision",
                "external_reference_sha256",
                "signature_base64",
            },
            f"attestation {index}",
        )
        if receipt["schema_version"] != ENTRY_ATTESTATION_SCHEMA_VERSION:
            raise MaturityG1EntryError("attestation receipt schema is unsupported")
        attestation_id = _safe_id(receipt["attestation_id"], "attestation id")
        if attestation_id in receipts:
            raise MaturityG1EntryError("attestation ids are not unique")
        kind = _string(receipt["kind"], "attestation kind")
        expected_role = _ATTESTATION_ROLE_BY_KIND.get(kind)
        if expected_role is None or receipt["issuer_role"] != expected_role:
            raise MaturityG1EntryError("attestation kind and issuer role do not match")
        _digest(receipt["subject_sha256"], "attestation subject digest")
        _git_hash(receipt["revision"], "attestation revision")
        key_id = _safe_id(receipt["issuer_key_id"], "attestation issuer key id")
        key = keys.get(key_id)
        if key is None or expected_role not in key["roles"]:
            raise MaturityG1EntryError("attestation issuer is not trusted for its role")
        issued_at = _time(receipt["issued_at"], "attestation issued_at")
        expires_at = _time(receipt["expires_at"], "attestation expires_at")
        if issued_at >= expires_at or not (issued_at <= verification_time < expires_at):
            raise MaturityG1EntryError("attestation is expired or not yet effective")
        if not (
            key["valid_from_parsed"] <= issued_at < key["expires_at_parsed"]
            and key["currently_valid"]
        ):
            raise MaturityG1EntryError("attestation signing key is expired, revoked, or not effective")
        if receipt["decision"] != "APPROVED":
            raise MaturityG1EntryError("attestation decision is not approved")
        _digest(receipt["external_reference_sha256"], "external reference digest")
        unsigned_receipt = {
            key_name: value
            for key_name, value in receipt.items()
            if key_name != "signature_base64"
        }
        try:
            signature = base64.b64decode(receipt["signature_base64"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MaturityG1EntryError("attestation signature is not canonical base64") from exc
        if len(signature) != 64:
            raise MaturityG1EntryError("attestation signature length is invalid")
        try:
            Ed25519PublicKey.from_public_bytes(key["public_bytes"]).verify(
                signature,
                attestation_signing_bytes(unsigned_receipt),
            )
        except (InvalidSignature, ValueError) as exc:
            raise MaturityG1EntryError("attestation signature is invalid") from exc
        receipts[attestation_id] = {
            **receipt,
            "receipt_sha256": attestation_receipt_sha256(receipt),
            "issued_at_parsed": issued_at,
            "expires_at_parsed": expires_at,
        }
    if tuple(receipts) != tuple(sorted(receipts)):
        raise MaturityG1EntryError("attestations must be sorted by attestation_id")
    return receipts


def _validate_revision(value: object) -> dict[str, Any]:
    row = _expect_keys(
        value,
        {
            "repository_logical_id",
            "commit_sha",
            "tree_sha",
            "owner_review_attestation_id",
            "owner_review_attestation_sha256",
            "reviewed_at",
            "protected_ref",
        },
        "reviewed revision",
    )
    _safe_id(row["repository_logical_id"], "repository logical id")
    _git_hash(row["commit_sha"], "reviewed commit")
    _git_hash(row["tree_sha"], "reviewed tree")
    _reference_pair(row, "owner_review", "owner review")
    if row["reviewed_at"] is not None:
        _time(row["reviewed_at"], "reviewed_at")
    protected_ref = _string(row["protected_ref"], "protected ref")
    if not protected_ref.startswith("refs/"):
        raise MaturityG1EntryError("protected ref is not a logical Git ref")
    return row


def _validate_g0(value: object) -> dict[str, Any]:
    row = _expect_keys(
        value,
        {
            "manifest_schema_version",
            "manifest_content_sha256",
            "source_snapshot_sha256",
            "file_count",
            "scope_counts",
            "exact_verified",
            "exclusions_approved",
            "unapproved_exclusion_addition_count",
            "release_blocker_count",
            "cleanroom_receipt_sha256",
            "cleanroom_release_ready",
        },
        "G0 admission",
    )
    _safe_id(row["manifest_schema_version"], "G0 schema version")
    _digest(row["manifest_content_sha256"], "G0 manifest digest")
    _digest(row["source_snapshot_sha256"], "G0 source snapshot")
    _integer(row["file_count"], "G0 file count", minimum=1)
    if not isinstance(row["scope_counts"], dict) or not row["scope_counts"]:
        raise MaturityG1EntryError("G0 scope counts are missing")
    if tuple(row["scope_counts"]) != tuple(sorted(row["scope_counts"])):
        raise MaturityG1EntryError("G0 scope counts must be sorted")
    if any(
        not isinstance(scope, str)
        or not scope
        or _integer(count, f"G0 scope {scope}", minimum=1) < 1
        for scope, count in row["scope_counts"].items()
    ):
        raise MaturityG1EntryError("G0 scope count is invalid")
    if sum(row["scope_counts"].values()) != row["file_count"]:
        raise MaturityG1EntryError("G0 scope counts do not match file count")
    _boolean(row["exact_verified"], "G0 exact flag")
    _boolean(row["exclusions_approved"], "G0 exclusions flag")
    _integer(row["unapproved_exclusion_addition_count"], "G0 exclusion additions")
    _integer(row["release_blocker_count"], "G0 release blocker count")
    if row["cleanroom_receipt_sha256"] is not None:
        _digest(row["cleanroom_receipt_sha256"], "cleanroom receipt digest")
    _boolean(row["cleanroom_release_ready"], "cleanroom release-ready flag")
    return row


def _validate_remote_ci(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    row = _expect_keys(
        value,
        {
            "provider",
            "repository_logical_id",
            "workflow_logical_id",
            "workflow_source_sha256",
            "run_id",
            "run_attempt",
            "trigger",
            "protected_ref",
            "revision",
            "source_snapshot_sha256",
            "started_at",
            "completed_at",
            "conclusion",
            "jobs",
            "attempt_history",
            "ci_attestation_id",
            "ci_attestation_sha256",
        },
        "remote CI",
    )
    for name in (
        "provider",
        "repository_logical_id",
        "workflow_logical_id",
        "run_id",
        "trigger",
    ):
        _safe_id(row[name], f"remote CI {name}")
    _digest(row["workflow_source_sha256"], "workflow source digest")
    _integer(row["run_attempt"], "remote CI run attempt", minimum=1)
    if not _string(row["protected_ref"], "remote CI protected ref").startswith("refs/"):
        raise MaturityG1EntryError("remote CI ref is not protected-ref shaped")
    _git_hash(row["revision"], "remote CI revision")
    _digest(row["source_snapshot_sha256"], "remote CI source snapshot")
    started = _time(row["started_at"], "remote CI started_at")
    completed = _time(row["completed_at"], "remote CI completed_at")
    if started >= completed:
        raise MaturityG1EntryError("remote CI interval is empty")
    if row["conclusion"] != "success":
        raise MaturityG1EntryError("remote CI conclusion is not success")
    _reference_pair(row, "ci", "remote CI")
    if not isinstance(row["attempt_history"], list) or not row["attempt_history"]:
        raise MaturityG1EntryError("remote CI attempt history is missing")
    attempt_numbers: list[int] = []
    for index, raw_attempt in enumerate(row["attempt_history"]):
        attempt = _expect_keys(
            raw_attempt,
            {
                "run_attempt",
                "conclusion",
                "started_at",
                "completed_at",
                "log_sha256",
                "artifact_set_sha256",
            },
            f"remote CI attempt {index}",
        )
        attempt_number = _integer(
            attempt["run_attempt"],
            "remote CI historical attempt",
            minimum=1,
        )
        attempt_numbers.append(attempt_number)
        if attempt["conclusion"] not in {"success", "failure", "cancelled"}:
            raise MaturityG1EntryError("remote CI attempt conclusion is unsupported")
        attempt_started = _time(attempt["started_at"], "remote CI attempt started_at")
        attempt_completed = _time(attempt["completed_at"], "remote CI attempt completed_at")
        if attempt_started >= attempt_completed:
            raise MaturityG1EntryError("remote CI attempt interval is empty")
        _digest(attempt["log_sha256"], "remote CI attempt log digest")
        _digest(attempt["artifact_set_sha256"], "remote CI attempt artifact digest")
    if attempt_numbers != list(range(1, row["run_attempt"] + 1)):
        raise MaturityG1EntryError("remote CI attempt history is incomplete or overwritten")
    if row["attempt_history"][-1]["conclusion"] != row["conclusion"]:
        raise MaturityG1EntryError("remote CI current conclusion disagrees with attempt history")
    if not isinstance(row["jobs"], list) or not row["jobs"]:
        raise MaturityG1EntryError("remote CI jobs are missing")
    job_ids: list[str] = []
    for index, raw_job in enumerate(row["jobs"]):
        job = _expect_keys(
            raw_job,
            {
                "job_id",
                "purpose",
                "python_version",
                "conclusion",
                "stages",
                "command_set_sha256",
                "log_sha256",
                "artifact_set_sha256",
            },
            f"remote CI job {index}",
        )
        job_ids.append(_safe_id(job["job_id"], "remote CI job id"))
        _safe_id(job["purpose"], "remote CI job purpose")
        if job["python_version"] is not None:
            _string(job["python_version"], "remote CI Python version")
        if job["conclusion"] != "success":
            raise MaturityG1EntryError("remote CI job conclusion is not success")
        _ordered_strings(job["stages"], "remote CI job stages", allow_empty=False)
        for digest_name in ("command_set_sha256", "log_sha256", "artifact_set_sha256"):
            _digest(job[digest_name], f"remote CI {digest_name}")
    if tuple(job_ids) != tuple(sorted(set(job_ids))):
        raise MaturityG1EntryError("remote CI jobs must be sorted and unique")
    return row


def _validate_generated_boundary(value: object) -> dict[str, Any]:
    row = _expect_keys(
        value,
        {
            "tracked_generated_path_count",
            "release_blocker_count",
            "git_clean",
            "build_reconstruction_passed",
            "build_contract_sha256",
        },
        "generated output boundary",
    )
    _integer(row["tracked_generated_path_count"], "tracked generated path count")
    _integer(row["release_blocker_count"], "generated release blocker count")
    _boolean(row["git_clean"], "generated boundary Git clean")
    _boolean(row["build_reconstruction_passed"], "build reconstruction flag")
    _digest(row["build_contract_sha256"], "build contract digest")
    return row


def _validate_supply_chain(value: object) -> dict[str, Any]:
    row = _expect_keys(
        value,
        {
            "lockfile_sha256",
            "sbom_sha256",
            "audit_tool_version",
            "finding_set_sha256",
            "unaccepted_critical_count",
            "unaccepted_high_count",
            "exceptions",
        },
        "supply-chain disposition",
    )
    for name in ("lockfile_sha256", "sbom_sha256", "finding_set_sha256"):
        _digest(row[name], f"supply-chain {name}")
    _safe_id(row["audit_tool_version"], "audit tool version")
    _integer(row["unaccepted_critical_count"], "unaccepted critical count")
    _integer(row["unaccepted_high_count"], "unaccepted high count")
    if not isinstance(row["exceptions"], list):
        raise MaturityG1EntryError("supply-chain exceptions are missing")
    finding_ids: list[str] = []
    for index, raw_exception in enumerate(row["exceptions"]):
        exception = _expect_keys(
            raw_exception,
            {
                "finding_id",
                "scope",
                "mitigation",
                "owner_attestation_id",
                "owner_attestation_sha256",
                "expires_at",
                "closure_condition",
                "forbidden_release_stages",
            },
            f"supply-chain exception {index}",
        )
        finding_ids.append(_safe_id(exception["finding_id"], "supply finding id"))
        _string(exception["scope"], "supply exception scope")
        _string(exception["mitigation"], "supply exception mitigation")
        _reference_pair(exception, "owner", "security exception")
        _time(exception["expires_at"], "supply exception expiry")
        _string(exception["closure_condition"], "supply exception closure")
        forbidden = _sorted_strings(
            exception["forbidden_release_stages"],
            "forbidden release stages",
            allow_empty=False,
        )
        if forbidden != _FORBIDDEN_RELEASE_STAGES:
            raise MaturityG1EntryError("supply exception does not forbid every release stage")
    if tuple(finding_ids) != tuple(sorted(set(finding_ids))):
        raise MaturityG1EntryError("supply exceptions must be sorted and unique")
    return row


def _validate_decisions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise MaturityG1EntryError("architecture decisions are missing")
    decisions: list[dict[str, Any]] = []
    for index, raw_decision in enumerate(value):
        decision = _expect_keys(
            raw_decision,
            {
                "decision_id",
                "choice",
                "status",
                "rationale_sha256",
                "constraints",
                "required_tests",
                "rollback_owner_attestation_id",
                "rollback_owner_attestation_sha256",
                "architecture_attestation_id",
                "architecture_attestation_sha256",
                "effective_revision",
                "expires_at",
            },
            f"architecture decision {index}",
        )
        _safe_id(decision["decision_id"], "decision id")
        _safe_id(decision["choice"], "decision choice")
        if decision["status"] not in _DECISION_STATUSES:
            raise MaturityG1EntryError("architecture decision status is unsupported")
        _digest(decision["rationale_sha256"], "decision rationale digest")
        _sorted_strings(decision["constraints"], "decision constraints")
        _sorted_strings(decision["required_tests"], "decision required tests", allow_empty=False)
        _reference_pair(decision, "rollback_owner", "rollback ownership")
        _reference_pair(decision, "architecture", "architecture decision")
        _git_hash(decision["effective_revision"], "decision effective revision")
        _optional_time(decision["expires_at"], "decision expiry")
        decisions.append(decision)
    if tuple(item["decision_id"] for item in decisions) != _DECISION_IDS:
        raise MaturityG1EntryError("architecture decisions must be exactly D1 through D5")
    return decisions


def _validate_data_authorization(value: object) -> dict[str, Any]:
    row = _expect_keys(
        value,
        {
            "mode",
            "dataset_logical_id",
            "snapshot_sha256",
            "schema_sha256",
            "watermark",
            "stable_input",
            "owner_attestation_id",
            "owner_attestation_sha256",
            "allowed_operations",
            "forbidden_operations",
            "retention",
            "expires_at",
        },
        "data authorization",
    )
    if row["mode"] not in {"fixture", "sanitized-mirror", "authorized-production"}:
        raise MaturityG1EntryError("data authorization mode is unsupported")
    _safe_id(row["dataset_logical_id"], "dataset logical id")
    _digest(row["snapshot_sha256"], "dataset snapshot digest")
    _digest(row["schema_sha256"], "dataset schema digest")
    _string(row["watermark"], "dataset watermark")
    _boolean(row["stable_input"], "stable input flag")
    _reference_pair(row, "owner", "data authorization")
    _sorted_strings(row["allowed_operations"], "allowed data operations", allow_empty=False)
    forbidden = _sorted_strings(
        row["forbidden_operations"],
        "forbidden data operations",
        allow_empty=False,
    )
    if row["mode"] != "authorized-production" and "connect-production" not in forbidden:
        raise MaturityG1EntryError("non-production data authorization must forbid production connect")
    _string(row["retention"], "data retention")
    _time(row["expires_at"], "data authorization expiry")
    return row


def _validate_independent_review(value: object) -> dict[str, Any]:
    row = _expect_keys(
        value,
        {
            "reviewed_revision",
            "scope_tasks",
            "implementation_paths",
            "review_attestation_id",
            "review_attestation_sha256",
            "reviewed_at",
        },
        "independent review",
    )
    _git_hash(row["reviewed_revision"], "independent review revision")
    _sorted_strings(row["scope_tasks"], "independent review tasks", allow_empty=False)
    _sorted_strings(row["implementation_paths"], "independent implementation paths")
    _reference_pair(row, "review", "independent review")
    _time(row["reviewed_at"], "independent review time")
    return row


def _validate_runtime_boundary(value: object) -> dict[str, Any]:
    row = _expect_keys(
        value,
        {
            "default_engine",
            "production_database_accessed",
            "release_authorized",
            "default_pointer_changed",
            "formal_schema_mutated",
            "config_snapshot_sha256",
            "release_registry_sha256",
            "boundary_attestation_id",
            "boundary_attestation_sha256",
        },
        "runtime boundary",
    )
    _safe_id(row["default_engine"], "default engine")
    for name in (
        "production_database_accessed",
        "release_authorized",
        "default_pointer_changed",
        "formal_schema_mutated",
    ):
        _boolean(row[name], f"runtime boundary {name}")
    _digest(row["config_snapshot_sha256"], "config snapshot digest")
    _digest(row["release_registry_sha256"], "release registry digest")
    _reference_pair(row, "boundary", "runtime boundary")
    return row


def _validate_requirements(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise MaturityG1EntryError("entry requirements are missing")
    rows: list[dict[str, Any]] = []
    for index, raw_row in enumerate(value):
        row = _expect_keys(
            raw_row,
            {"requirement_id", "status", "reason", "evidence_sha256"},
            f"requirement {index}",
        )
        _safe_id(row["requirement_id"], "requirement id")
        if row["status"] not in {"PASS", "HOLD"}:
            raise MaturityG1EntryError("requirement status is unsupported")
        _safe_id(row["reason"], "requirement reason")
        _digest(row["evidence_sha256"], "requirement evidence digest")
        rows.append(row)
    if tuple(row["requirement_id"] for row in rows) != _ENTRY_IDS:
        raise MaturityG1EntryError("entry requirements must be exactly E-01 through E-10")
    return rows


def _validate_packet(payload: dict[str, Any]) -> dict[str, Any]:
    _expect_keys(
        payload,
        {
            "schema_version",
            "artifact_version",
            "status",
            "reviewed_revision",
            "g0_admission",
            "remote_ci",
            "generated_output_boundary",
            "supply_chain_disposition",
            "architecture_decisions",
            "data_authorization",
            "independent_review",
            "runtime_boundary",
            "requirements",
            "limitations",
            "blockers",
            "attestation_set_sha256",
            "content_sha256",
        },
        "entry packet",
    )
    if payload["schema_version"] != ENTRY_PACKET_SCHEMA_VERSION or payload["artifact_version"] != "1":
        raise MaturityG1EntryError("entry packet schema or artifact version is unsupported")
    if payload["status"] not in _PACKET_STATUSES:
        raise MaturityG1EntryError("entry packet status is unsupported")
    unsigned = {key: value for key, value in payload.items() if key != "content_sha256"}
    if payload["content_sha256"] != packet_content_sha256(unsigned):
        raise MaturityG1EntryError("entry packet content digest mismatch")
    _validate_revision(payload["reviewed_revision"])
    _validate_g0(payload["g0_admission"])
    _validate_remote_ci(payload["remote_ci"])
    _validate_generated_boundary(payload["generated_output_boundary"])
    _validate_supply_chain(payload["supply_chain_disposition"])
    _validate_decisions(payload["architecture_decisions"])
    _validate_data_authorization(payload["data_authorization"])
    _validate_independent_review(payload["independent_review"])
    _validate_runtime_boundary(payload["runtime_boundary"])
    _validate_requirements(payload["requirements"])
    _sorted_strings(payload["limitations"], "packet limitations")
    _sorted_strings(payload["blockers"], "packet blockers")
    _digest(payload["attestation_set_sha256"], "attestation set digest")
    _security_scan(payload)
    return payload


def _subject_without(value: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in keys}


def _remote_ci_subject(packet: Mapping[str, Any]) -> dict[str, Any]:
    remote = packet["remote_ci"]
    if remote is None:
        raise MaturityG1EntryError("remote CI subject is unavailable")
    return {
        "g0_admission": packet["g0_admission"],
        "generated_output_boundary": packet["generated_output_boundary"],
        "remote_ci": _subject_without(
            remote,
            "ci_attestation_id",
            "ci_attestation_sha256",
        ),
        "supply_chain_disposition": packet["supply_chain_disposition"],
    }


def _receipt_matches(
    reference: tuple[str, str] | None,
    *,
    receipts: Mapping[str, Mapping[str, Any]],
    kind: str,
    revision: str,
    subject: Mapping[str, Any],
) -> bool:
    if reference is None:
        return False
    attestation_id, expected_digest = reference
    receipt = receipts.get(attestation_id)
    return bool(
        receipt
        and receipt["kind"] == kind
        and receipt["revision"] == revision
        and receipt["subject_sha256"] == attestation_subject_sha256(kind, subject)
        and receipt["receipt_sha256"] == expected_digest
    )


def _requirement(
    requirement_id: str,
    passed: bool,
    reason: str,
    evidence: object,
) -> dict[str, Any]:
    return {
        "requirement_id": requirement_id,
        "status": "PASS" if passed else "HOLD",
        "reason": "verified" if passed else reason,
        "evidence_sha256": domain_sha256_v1(
            f"rag-g1-entry-{requirement_id.lower()}-evidence-v1",
            evidence,
        ),
    }


def compute_entry_requirements(
    packet: Mapping[str, Any],
    receipts: Mapping[str, Mapping[str, Any]],
    *,
    verification_time: datetime,
) -> list[dict[str, Any]]:
    revision = packet["reviewed_revision"]
    commit_sha = revision["commit_sha"]
    owner_subject = _subject_without(
        revision,
        "owner_review_attestation_id",
        "owner_review_attestation_sha256",
    )
    e01 = _receipt_matches(
        _reference_pair(revision, "owner_review", "owner review"),
        receipts=receipts,
        kind="OWNER_REVIEW",
        revision=commit_sha,
        subject=owner_subject,
    ) and revision["reviewed_at"] is not None

    g0 = packet["g0_admission"]
    remote = packet["remote_ci"]
    e02 = bool(
        g0["exact_verified"]
        and g0["exclusions_approved"]
        and g0["unapproved_exclusion_addition_count"] == 0
        and remote is not None
        and remote["source_snapshot_sha256"] == g0["source_snapshot_sha256"]
    )

    ci_receipt = False
    jobs_by_purpose: dict[str, Mapping[str, Any]] = {}
    if remote is not None:
        ci_subject = _remote_ci_subject(packet)
        ci_receipt = _receipt_matches(
            _reference_pair(remote, "ci", "remote CI"),
            receipts=receipts,
            kind="REMOTE_CI",
            revision=commit_sha,
            subject=ci_subject,
        )
        jobs_by_purpose = {job["purpose"]: job for job in remote["jobs"]}
    py312 = jobs_by_purpose.get("backend-full-py312")
    py313 = jobs_by_purpose.get("backend-full-py313")
    e03 = bool(
        remote
        and ci_receipt
        and remote["revision"] == commit_sha
        and remote["repository_logical_id"] == revision["repository_logical_id"]
        and py312
        and py313
        and py312["python_version"] == "3.12"
        and py313["python_version"] == "3.13"
        and py312["stages"] == ["backend-full"]
        and py313["stages"] == ["backend-full"]
    )
    admission_job = jobs_by_purpose.get("admission")
    e04 = bool(
        remote
        and ci_receipt
        and admission_job
        and tuple(admission_job["stages"]) == _ADMISSION_STAGES
        and admission_job["python_version"] is None
        and g0["cleanroom_receipt_sha256"] is not None
        and g0["cleanroom_release_ready"]
    )

    generated = packet["generated_output_boundary"]
    e05 = bool(
        generated["tracked_generated_path_count"] == 0
        and generated["release_blocker_count"] == 0
        and generated["git_clean"]
        and generated["build_reconstruction_passed"]
        and g0["release_blocker_count"] == 0
    )

    supply = packet["supply_chain_disposition"]
    exception_receipts = True
    for exception in supply["exceptions"]:
        exception_subject = _subject_without(
            exception,
            "owner_attestation_id",
            "owner_attestation_sha256",
        )
        exception_receipts = exception_receipts and _receipt_matches(
            _reference_pair(exception, "owner", "security exception"),
            receipts=receipts,
            kind="SECURITY_EXCEPTION",
            revision=commit_sha,
            subject=exception_subject,
        )
        exception_receipts = exception_receipts and (
            _time(exception["expires_at"], "supply exception expiry") > verification_time
        )
    e06 = bool(
        supply["unaccepted_critical_count"] == 0
        and supply["unaccepted_high_count"] == 0
        and exception_receipts
    )

    decision_receipts = True
    architecture_key_ids: set[str] = set()
    rollback_key_ids: set[str] = set()
    for decision in packet["architecture_decisions"]:
        decision_subject = _subject_without(
            decision,
            "architecture_attestation_id",
            "architecture_attestation_sha256",
            "rollback_owner_attestation_id",
            "rollback_owner_attestation_sha256",
        )
        architecture_ref = _reference_pair(decision, "architecture", "architecture decision")
        rollback_ref = _reference_pair(decision, "rollback_owner", "rollback ownership")
        architecture_receipt = receipts.get(architecture_ref[0]) if architecture_ref else None
        rollback_receipt = receipts.get(rollback_ref[0]) if rollback_ref else None
        if architecture_receipt:
            architecture_key_ids.add(architecture_receipt["issuer_key_id"])
        if rollback_receipt:
            rollback_key_ids.add(rollback_receipt["issuer_key_id"])
        decision_receipts = decision_receipts and bool(
            decision["status"] in {"ACCEPTED", "ACCEPTED_WITH_REVIEWED_ADR"}
            and decision["effective_revision"] == commit_sha
            and _receipt_matches(
                architecture_ref,
                receipts=receipts,
                kind="ARCHITECTURE_DECISION",
                revision=commit_sha,
                subject=decision_subject,
            )
            and _receipt_matches(
                rollback_ref,
                receipts=receipts,
                kind="ROLLBACK_OWNERSHIP",
                revision=commit_sha,
                subject=decision_subject,
            )
            and (
                decision["expires_at"] is None
                or _time(decision["expires_at"], "decision expiry") > verification_time
            )
        )
    e07 = decision_receipts and architecture_key_ids.isdisjoint(rollback_key_ids)

    data = packet["data_authorization"]
    data_subject = _subject_without(data, "owner_attestation_id", "owner_attestation_sha256")
    data_ref = _reference_pair(data, "owner", "data authorization")
    e08 = bool(
        data["mode"] == "fixture"
        and data["stable_input"]
        and "connect-production" in data["forbidden_operations"]
        and _time(data["expires_at"], "data authorization expiry") > verification_time
        and _receipt_matches(
            data_ref,
            receipts=receipts,
            kind="DATA_AUTHORIZATION",
            revision=commit_sha,
            subject=data_subject,
        )
    )

    independent = packet["independent_review"]
    independent_subject = _subject_without(
        independent,
        "review_attestation_id",
        "review_attestation_sha256",
    )
    independent_ref = _reference_pair(independent, "review", "independent review")
    independent_receipt = receipts.get(independent_ref[0]) if independent_ref else None
    owner_ref = _reference_pair(revision, "owner_review", "owner review")
    owner_receipt = receipts.get(owner_ref[0]) if owner_ref else None
    e09 = bool(
        independent["reviewed_revision"] == commit_sha
        and independent["scope_tasks"] == ["T1.1.1:C1-C10"]
        and independent["implementation_paths"] == []
        and _receipt_matches(
            independent_ref,
            receipts=receipts,
            kind="INDEPENDENT_REVIEW",
            revision=commit_sha,
            subject=independent_subject,
        )
        and independent_receipt
        and owner_receipt
        and independent_receipt["issuer_key_id"] != owner_receipt["issuer_key_id"]
    )

    runtime = packet["runtime_boundary"]
    runtime_subject = _subject_without(
        runtime,
        "boundary_attestation_id",
        "boundary_attestation_sha256",
    )
    runtime_ref = _reference_pair(runtime, "boundary", "runtime boundary")
    runtime_receipt = receipts.get(runtime_ref[0]) if runtime_ref else None
    e10 = bool(
        runtime["default_engine"] == "v1"
        and not runtime["production_database_accessed"]
        and not runtime["release_authorized"]
        and not runtime["default_pointer_changed"]
        and not runtime["formal_schema_mutated"]
        and _receipt_matches(
            runtime_ref,
            receipts=receipts,
            kind="RUNTIME_BOUNDARY",
            revision=commit_sha,
            subject=runtime_subject,
        )
        and runtime_receipt
        and independent_receipt
        and runtime_receipt["issuer_key_id"] != independent_receipt["issuer_key_id"]
    )

    conditions = (
        ("E-01", e01, "owner_review_attestation_missing_or_invalid", owner_subject),
        ("E-02", e02, "g0_admission_not_exact", g0),
        ("E-03", e03, "remote_python_matrix_missing_or_untrusted", remote),
        ("E-04", e04, "remote_admission_chain_missing_or_incomplete", remote),
        ("E-05", e05, "generated_output_boundary_not_closed", generated),
        ("E-06", e06, "supply_chain_not_accepted", supply),
        ("E-07", e07, "architecture_decisions_not_attested", packet["architecture_decisions"]),
        ("E-08", e08, "data_authorization_not_attested", data),
        ("E-09", e09, "independent_review_not_attested", independent),
        ("E-10", e10, "runtime_boundary_not_attested", runtime),
    )
    return [_requirement(*condition) for condition in conditions]


def _referenced_attestations(packet: Mapping[str, Any]) -> set[str]:
    references: set[str] = set()

    def add(reference: tuple[str, str] | None) -> None:
        if reference is not None:
            references.add(reference[0])

    add(_reference_pair(packet["reviewed_revision"], "owner_review", "owner review"))
    if packet["remote_ci"] is not None:
        add(_reference_pair(packet["remote_ci"], "ci", "remote CI"))
    for exception in packet["supply_chain_disposition"]["exceptions"]:
        add(_reference_pair(exception, "owner", "security exception"))
    for decision in packet["architecture_decisions"]:
        add(_reference_pair(decision, "architecture", "architecture decision"))
        add(_reference_pair(decision, "rollback_owner", "rollback ownership"))
    add(_reference_pair(packet["data_authorization"], "owner", "data authorization"))
    add(_reference_pair(packet["independent_review"], "review", "independent review"))
    add(_reference_pair(packet["runtime_boundary"], "boundary", "runtime boundary"))
    return references


def _plain_canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes_v1(value)).hexdigest()


def _verify_g0_manifest(path: Path, packet_g0: Mapping[str, Any]) -> str:
    manifest = _read_canonical_json(path, label="G0 admission manifest")
    try:
        verified = verify_admission_manifest(path)
    except MaturityG0AdmissionError as exc:
        raise MaturityG1EntryError(f"G0 manifest failed native verification: {exc}") from exc
    if manifest.get("schema_version") != packet_g0["manifest_schema_version"]:
        raise MaturityG1EntryError("G0 manifest schema does not match entry packet")
    manifest_digest = manifest.get("content_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "content_sha256"}
    if manifest_digest != _plain_canonical_sha256(unsigned):
        raise MaturityG1EntryError("G0 manifest content digest is invalid")
    if manifest_digest != packet_g0["manifest_content_sha256"]:
        raise MaturityG1EntryError("G0 manifest content digest does not match entry packet")
    if manifest.get("source_snapshot_sha256") != packet_g0["source_snapshot_sha256"]:
        raise MaturityG1EntryError("G0 source snapshot does not match entry packet")
    files = manifest.get("files")
    scopes = manifest.get("scopes")
    if not isinstance(files, list) or not isinstance(scopes, dict):
        raise MaturityG1EntryError("G0 manifest denominator inputs are missing")
    recomputed_scope_counts: dict[str, int] = {}
    for index, file_row in enumerate(files):
        if not isinstance(file_row, dict) or not isinstance(file_row.get("scope"), str):
            raise MaturityG1EntryError(f"G0 manifest file {index} has no scope")
        scope = file_row["scope"]
        recomputed_scope_counts[scope] = recomputed_scope_counts.get(scope, 0) + 1
    recomputed_scope_counts = dict(sorted(recomputed_scope_counts.items()))
    declared_scope_counts: dict[str, int] = {}
    for scope, summary in scopes.items():
        if not isinstance(summary, dict):
            raise MaturityG1EntryError("G0 manifest scope summary is invalid")
        declared_scope_counts[scope] = _integer(
            summary.get("file_count"),
            f"G0 manifest {scope} file count",
            minimum=0,
        )
    positive_declared_scope_counts = {
        scope: count for scope, count in declared_scope_counts.items() if count > 0
    }
    if positive_declared_scope_counts != recomputed_scope_counts:
        raise MaturityG1EntryError("G0 manifest scope summaries do not match file rows")
    if len(files) != packet_g0["file_count"]:
        raise MaturityG1EntryError("G0 file count was not recomputed from artifact rows")
    if recomputed_scope_counts != packet_g0["scope_counts"]:
        raise MaturityG1EntryError("G0 scope counts were not recomputed from artifact rows")
    if manifest.get("release_blocker_count") != packet_g0["release_blocker_count"]:
        raise MaturityG1EntryError("G0 release blocker count does not match entry packet")
    if (
        verified["content_sha256"] != manifest_digest
        or verified["source_snapshot_sha256"] != packet_g0["source_snapshot_sha256"]
        or verified["file_count"] != packet_g0["file_count"]
    ):
        raise MaturityG1EntryError("G0 native verification result does not match entry packet")
    return manifest_digest


def verify_entry_packet(
    packet_path: Path,
    *,
    verification_time: str,
    attestation_bundle_path: Path | None = None,
    keyset_path: Path | None = None,
    trusted_keyset_sha256: str | None = None,
    g0_admission_path: Path | None = None,
) -> dict[str, Any]:
    packet = _validate_packet(_read_canonical_json(packet_path, label="entry packet"))
    verified_at = _time(verification_time, "verification time")
    receipts: dict[str, dict[str, Any]] = {}
    keyset_digest: str | None = None
    if any((attestation_bundle_path, keyset_path, trusted_keyset_sha256)):
        if not all((attestation_bundle_path, keyset_path, trusted_keyset_sha256)):
            raise MaturityG1EntryError(
                "attestation bundle, keyset, and out-of-band keyset digest are all required"
            )
        keyset_payload = _read_canonical_json(keyset_path, label="trusted keyset")
        keys = _validate_keyset(
            keyset_payload,
            trusted_keyset_sha256=trusted_keyset_sha256,
            verification_time=verified_at,
        )
        bundle = _read_canonical_json(attestation_bundle_path, label="attestation bundle")
        receipts = _validate_receipts(bundle, keys=keys, verification_time=verified_at)
        keyset_digest = trusted_keyset_sha256
    referenced = _referenced_attestations(packet)
    if referenced != set(receipts):
        raise MaturityG1EntryError("packet and attestation bundle reference sets do not match")
    receipt_rows = [receipts[key] for key in sorted(receipts)]
    public_receipts = [
        {
            key: value
            for key, value in row.items()
            if not key.endswith("_parsed") and key != "receipt_sha256"
        }
        for row in receipt_rows
    ]
    if packet["attestation_set_sha256"] != attestation_set_sha256(public_receipts):
        raise MaturityG1EntryError("packet attestation-set digest mismatch")
    expected_requirements = compute_entry_requirements(
        packet,
        receipts,
        verification_time=verified_at,
    )
    if packet["requirements"] != expected_requirements:
        raise MaturityG1EntryError("packet requirement rows do not match recomputed evidence")
    qualified = all(row["status"] == "PASS" for row in expected_requirements)
    g0_manifest_digest: str | None = None
    if g0_admission_path is not None:
        g0_manifest_digest = _verify_g0_manifest(g0_admission_path, packet["g0_admission"])
    if qualified and g0_manifest_digest is None:
        raise MaturityG1EntryError("approved entry requires offline G0 manifest verification")
    if qualified != (packet["status"] == "APPROVED"):
        raise MaturityG1EntryError("packet status does not match the E-01..E-10 conjunction")
    if qualified != (packet["blockers"] == []):
        raise MaturityG1EntryError("packet blockers do not match qualification status")
    result: dict[str, Any] = {
        "schema_version": ENTRY_VERIFICATION_SCHEMA_VERSION,
        "packet_content_sha256": packet["content_sha256"],
        "trusted_keyset_sha256": keyset_digest,
        "g0_manifest_content_sha256": g0_manifest_digest,
        "attestation_set_sha256": packet["attestation_set_sha256"],
        "verified_attestation_ids": sorted(receipts),
        "verification_time": verification_time,
        "requirements": expected_requirements,
        "status": "ENTRY_QUALIFIED" if qualified else "ENTRY_NOT_SATISFIED",
        "default_engine": packet["runtime_boundary"]["default_engine"],
        "production_database_accessed": False,
        "release_authorized": False,
        "network_accessed": False,
        "database_accessed": False,
    }
    result["content_sha256"] = domain_sha256_v1("rag-g1-entry-verification-v1", result)
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("--at", required=True, dest="verification_time")
    parser.add_argument("--attestations", type=Path)
    parser.add_argument("--keyset", type=Path)
    parser.add_argument("--trusted-keyset-sha256")
    parser.add_argument("--g0-admission", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        result = verify_entry_packet(
            arguments.packet,
            verification_time=arguments.verification_time,
            attestation_bundle_path=arguments.attestations,
            keyset_path=arguments.keyset,
            trusted_keyset_sha256=arguments.trusted_keyset_sha256,
            g0_admission_path=arguments.g0_admission,
        )
    except (OSError, MaturityG1EntryError) as exc:
        parser.error(str(exc))
    print(canonical_json_bytes_v1(result).decode("utf-8"))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ENTRY_ATTESTATION_BUNDLE_SCHEMA_VERSION",
    "ENTRY_ATTESTATION_SCHEMA_VERSION",
    "ENTRY_KEYSET_SCHEMA_VERSION",
    "ENTRY_PACKET_SCHEMA_VERSION",
    "ENTRY_VERIFICATION_SCHEMA_VERSION",
    "MaturityG1EntryError",
    "attestation_bundle_content_sha256",
    "attestation_receipt_sha256",
    "attestation_set_sha256",
    "attestation_signing_bytes",
    "attestation_subject_sha256",
    "canonical_json_bytes_v1",
    "compute_entry_requirements",
    "domain_sha256_v1",
    "keyset_content_sha256",
    "packet_content_sha256",
    "verify_entry_packet",
]
