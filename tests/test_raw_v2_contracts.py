from __future__ import annotations

import locale
import os
import shutil
import subprocess
import sys
import zipfile
from collections import UserDict
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from evidence_rag.sources.v2.contracts import (
    AUDIT_SEVERITIES,
    CANONICAL_JSON_VERSION,
    EXCEPTION_CLASSES,
    H_HEX,
    OUTWARD_DISPOSITIONS,
    RAW_V2_CONTRACT_VERSION,
    REASON_POLICY,
    SIZE_LIMITS,
    SOURCE_TYPE_TO_DOMAIN,
    AuditSeverity,
    DigestDomain,
    ExceptionClass,
    H,
    OutwardDisposition,
    RawEvidenceBindingPayload,
    RawLogicalIdentityPayload,
    RawSelectorPayload,
    RawV2ContractError,
    RawV2Reason,
    ReasonPolicy,
    SizeLimit,
    SourceDomain,
    SourceEventIdempotencyPayload,
    VisibilityPartitionPayload,
    canonical_json_bytes,
    canonical_timestamp,
    derive_locator_id,
    derive_raw_object_id,
    derive_selector_sha256,
    derive_source_event_id,
    derive_visibility_partition_sha256,
    ensure_utf8_size,
    exact_bytes_sha256,
    source_domain_for_type,
    strict_json_loads,
    validate_locator_id,
    validate_raw_object_id,
    validate_sha256_digest,
    validate_source_event_id,
    verify_canonical_json_bytes,
    verify_canonical_timestamp,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/raw_v2/canonical-vectors-v1.json"


class _Model(BaseModel):
    value: str


class _Enum(StrEnum):
    VALUE = "value"


def _assert_reason(expected: RawV2Reason, call, *args, **kwargs) -> None:
    with pytest.raises(RawV2ContractError) as captured:
        call(*args, **kwargs)
    assert captured.value.reason is expected
    assert captured.value.policy == REASON_POLICY[expected]


def _visibility(project_id: str = "project-é", acl_ref: str = "acl-private") -> str:
    return derive_visibility_partition_sha256(
        VisibilityPartitionPayload(project_id=project_id, acl_ref=acl_ref)
    )


def _logical_payload(
    *,
    project_id: str = "project-é",
    raw_content_sha256: str | None = None,
) -> RawLogicalIdentityPayload:
    return RawLogicalIdentityPayload(
        project_id=project_id,
        source_domain=SourceDomain.CODE,
        source_type="git",
        source_instance_id="repo-main",
        source_object_id="src/app.py",
        source_version="commit-abc123",
        stable_version="blob-def456",
        raw_content_sha256=raw_content_sha256,
        acl_ref="acl-private",
        visibility_partition_sha256=_visibility(project_id=project_id),
    )


def _event_payload(*, raw_content_sha256: str | None = None) -> SourceEventIdempotencyPayload:
    return SourceEventIdempotencyPayload(
        project_id="project-é",
        source_domain=SourceDomain.CODE,
        source_type="git",
        source_instance_id="repo-main",
        source_object_id="src/app.py",
        source_version="commit-abc123",
        event_type="upsert",
        raw_content_sha256=raw_content_sha256,
        acl_ref="acl-private",
        visibility_partition_sha256=_visibility(),
        mutation_id="mutation-001",
    )


def _selector_payload() -> RawSelectorPayload:
    return RawSelectorPayload(
        selector_kind="line_range",
        selector={"end_line": 8, "include_context": True, "start_line": 3},
    )


def _binding_payload() -> RawEvidenceBindingPayload:
    raw_digest = exact_bytes_sha256(b"print('authority')\n")
    selector_digest = derive_selector_sha256(_selector_payload())
    return RawEvidenceBindingPayload(
        project_id="project-é",
        source_domain=SourceDomain.CODE,
        raw_object_id=derive_raw_object_id(_logical_payload(raw_content_sha256=raw_digest)),
        source_version="commit-abc123",
        stable_version="blob-def456",
        generation_id="generation-001",
        derived_entity_id="entity-app",
        retrieval_unit_id="unit-lines-3-8",
        derivation_kind="source_slice",
        derivation_version="derivation-v1",
        selector_sha256=selector_digest,
        raw_content_sha256=raw_digest,
        selected_content_sha256=exact_bytes_sha256(b"authority"),
        parser_artifact_sha256=None,
        valid_from="2026-08-31T14:00:00Z",
        valid_to=None,
        observed_at="2026-08-31T14:00:01.250000+00:00",
        acl_ref="acl-private",
        visibility_partition_sha256=_visibility(),
        adapter_version="git-adapter-v2",
        schema_version="raw-binding-v2",
    )


def _fixture_payload(path: Path = FIXTURE) -> dict[str, object]:
    raw = path.read_bytes()
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    payload = verify_canonical_json_bytes(raw[:-1], allow_none=True)
    assert isinstance(payload, dict)
    content_digest = payload["content_sha256"]
    unsigned = {key: value for key, value in payload.items() if key != "content_sha256"}
    assert exact_bytes_sha256(canonical_json_bytes(unsigned, allow_none=True)) == content_digest
    vectors = payload["vectors"]
    assert exact_bytes_sha256(canonical_json_bytes(vectors, allow_none=True)) == payload[
        "vector_set_sha256"
    ]
    return payload


def test_c1_exact_value_domain_and_explicit_null() -> None:
    assert canonical_json_bytes({"z": [True, 1, "e\u0301"], "a": None}, allow_none=True) == (
        '{"a":null,"z":[true,1,"é"]}'.encode()
    )
    assert canonical_json_bytes([True, 1]) == b"[true,1]"
    _assert_reason(RawV2Reason.CANONICALIZATION_MISMATCH, canonical_json_bytes, None)

    unsupported = [
        1.25,
        Decimal("1.25"),
        b"bytes",
        bytearray(b"bytes"),
        datetime.now(UTC),
        _Enum.VALUE,
        object(),
        UserDict({"a": 1}),
        {"a"},
        frozenset({"a"}),
        ("a",),
        _Model(value="a"),
    ]
    for value in unsupported:
        _assert_reason(RawV2Reason.CANONICALIZATION_MISMATCH, canonical_json_bytes, value)


def test_c2_recursive_nfc_and_collision_rejection() -> None:
    composed = {"café": ["résumé", {"é": "déjà"}]}
    decomposed = {"cafe\u0301": ["re\u0301sume\u0301", {"e\u0301": "de\u0301ja\u0300"}]}
    assert canonical_json_bytes(composed) == canonical_json_bytes(decomposed)
    assert canonical_json_bytes({"é": 1, "z": 2}) == '{"z":2,"é":1}'.encode()
    _assert_reason(
        RawV2Reason.CANONICAL_DUPLICATE_KEY,
        canonical_json_bytes,
        {"é": 1, "e\u0301": 2},
    )
    _assert_reason(RawV2Reason.CANONICALIZATION_MISMATCH, canonical_json_bytes, {1: "bad"})


@pytest.mark.parametrize(
    "raw,reason",
    [
        (b'{"a":1,"a":2}', RawV2Reason.CANONICAL_DUPLICATE_KEY),
        ('{"é":1,"e\\u0301":2}', RawV2Reason.CANONICAL_DUPLICATE_KEY),
        (b'{"value":1.0}', RawV2Reason.CANONICALIZATION_MISMATCH),
        (b'{"value":1e2}', RawV2Reason.CANONICALIZATION_MISMATCH),
        (b'{"value":NaN}', RawV2Reason.CANONICALIZATION_MISMATCH),
        (b'{"value":Infinity}', RawV2Reason.CANONICALIZATION_MISMATCH),
        (b'{"value":-Infinity}', RawV2Reason.CANONICALIZATION_MISMATCH),
        (b'{"value":1} trailing', RawV2Reason.CANONICALIZATION_MISMATCH),
        (b'\xff', RawV2Reason.CANONICALIZATION_MISMATCH),
    ],
)
def test_c3_strict_loader_rejects_ambiguous_json(raw: bytes | str, reason: RawV2Reason) -> None:
    _assert_reason(reason, strict_json_loads, raw, allow_none=True)


def test_c3_parser_direct_parity_and_exact_verification() -> None:
    raw = '{"nested":{"cafe\\u0301":"re\\u0301sume\\u0301"},"nullable":null}'
    parsed_text = strict_json_loads(raw, allow_none=True)
    parsed_bytes = strict_json_loads(raw.encode(), allow_none=True)
    assert parsed_text == parsed_bytes == {"nested": {"café": "résumé"}, "nullable": None}
    assert canonical_json_bytes(parsed_text, allow_none=True) == (
        '{"nested":{"café":"résumé"},"nullable":null}'.encode()
    )
    assert verify_canonical_json_bytes(
        canonical_json_bytes(parsed_text, allow_none=True), allow_none=True
    ) == parsed_text
    _assert_reason(
        RawV2Reason.CANONICALIZATION_MISMATCH,
        verify_canonical_json_bytes,
        raw,
        allow_none=True,
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-08-31T14:00:00Z", "2026-08-31T14:00:00.000000Z"),
        ("2026-08-31T22:00:00+08:00", "2026-08-31T14:00:00.000000Z"),
        ("2026-08-31T09:30:00-04:30", "2026-08-31T14:00:00.000000Z"),
        ("2026-08-31T14:00:00.1234Z", "2026-08-31T14:00:00.123400Z"),
    ],
)
def test_c4_canonical_timestamp(value: str, expected: str) -> None:
    assert canonical_timestamp(value) == expected
    assert verify_canonical_timestamp(expected) == expected


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-31T14:00:00",
        "2026-08-31 14:00:00Z",
        "2026-08-31T14:00Z",
        "2026-08-31T14:00:60Z",
        "2026-02-30T14:00:00Z",
        "2026-08-31T14:00:00.1234567Z",
        "2026-08-31T14:00:00+00:60",
        "2026-08-31T14:00:00+24:00",
        "2026-08-31T14:00:00-00:00",
        "0001-01-01T00:00:00+01:00",
        "9999-12-31T23:59:59-01:00",
    ],
)
def test_c4_rejects_invalid_or_lossy_time(value: str) -> None:
    _assert_reason(RawV2Reason.TIMESTAMP_INVALID, canonical_timestamp, value)


def test_c5_frozen_domains_and_independent_golden_literals() -> None:
    fixture = _fixture_payload()
    vectors = fixture["vectors"]
    assert isinstance(vectors, dict)
    digest_vectors = vectors["digests"]
    assert isinstance(digest_vectors, list)
    for vector in digest_vectors:
        assert isinstance(vector, dict)
        domain = DigestDomain(vector["domain"])
        value = vector["value"]
        assert canonical_json_bytes(value, allow_none=True).decode() == vector["canonical_json"]
        assert H_HEX(domain, value, allow_none=True) == vector["h_hex"]
        assert H(domain, value, allow_none=True) == vector["h"]
    _assert_reason(
        RawV2Reason.CANONICALIZATION_MISMATCH,
        H,
        DigestDomain.RAW_LOGICAL_IDENTITY.value,
        {"a": 1},
    )
    assert exact_bytes_sha256(b"") == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    _assert_reason(RawV2Reason.CANONICALIZATION_MISMATCH, exact_bytes_sha256, bytearray())


def test_c6_frozen_identity_models_and_nullable_reference_identity() -> None:
    logical = _logical_payload()
    assert logical.raw_content_sha256 is None
    assert logical.canonical_value()["raw_content_sha256"] is None
    assert logical.project_id == "project-é"
    with pytest.raises(ValidationError):
        RawLogicalIdentityPayload(**{**logical.model_dump(), "unknown": "value"})
    with pytest.raises(ValidationError):
        logical.project_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _logical_payload().model_copy(update={"visibility_partition_sha256": "sha256:" + "0" * 64})
    with pytest.raises(ValidationError):
        RawLogicalIdentityPayload(
            **{**logical.model_dump(), "source_domain": SourceDomain.DOCUMENT}
        )
    with pytest.raises(ValidationError):
        RawLogicalIdentityPayload(**{**logical.model_dump(), "source_domain": "code"})
    with pytest.raises(ValidationError):
        RawLogicalIdentityPayload(**{**logical.model_dump(), "acl_ref": None})
    with pytest.raises(ValidationError):
        _logical_payload(project_id="project\ncontrol")

    reference_id = derive_raw_object_id(logical)
    active_id = derive_raw_object_id(
        _logical_payload(raw_content_sha256=exact_bytes_sha256(b"active"))
    )
    assert reference_id != active_id


def test_c6_binding_time_and_digest_invariants() -> None:
    binding = _binding_payload()
    assert binding.observed_at == "2026-08-31T14:00:01.250000Z"
    assert binding.valid_from == "2026-08-31T14:00:00.000000Z"
    with pytest.raises(ValidationError):
        RawEvidenceBindingPayload(
            **{
                **binding.model_dump(),
                "valid_from": "2026-09-01T00:00:00Z",
                "valid_to": "2026-08-31T00:00:00Z",
            }
        )
    with pytest.raises(ValidationError):
        RawEvidenceBindingPayload(
            **{**binding.model_dump(), "raw_content_sha256": "sha256:" + "A" * 64}
        )


def test_c7_deterministic_typed_ids_and_validation() -> None:
    raw_id = derive_raw_object_id(_logical_payload())
    event_id = derive_source_event_id(_event_payload())
    locator_id = derive_locator_id(_binding_payload())
    assert raw_id == derive_raw_object_id(_logical_payload())
    assert event_id == derive_source_event_id(_event_payload())
    assert locator_id == derive_locator_id(_binding_payload())
    assert validate_raw_object_id(raw_id) == raw_id
    assert validate_source_event_id(event_id) == event_id
    assert validate_locator_id(locator_id) == locator_id
    for invalid in (
        "raw-v2:sha256:" + "0" * 64,
        "raw-v2:" + "A" * 64,
        "raw-v2:" + "0" * 63,
        "source-event-v2:" + "0" * 64,
        "550e8400-e29b-41d4-a716-446655440000",
    ):
        _assert_reason(RawV2Reason.CANONICALIZATION_MISMATCH, validate_raw_object_id, invalid)
    assert derive_raw_object_id(_logical_payload(project_id="project-other")) != raw_id


def test_c8_six_source_registry_fails_closed() -> None:
    expected = {
        "git": SourceDomain.CODE,
        "codex": SourceDomain.CODEX,
        "mlflow": SourceDomain.EXPERIMENT,
        "notebook": SourceDomain.NOTEBOOK,
        "document": SourceDomain.DOCUMENT,
        "workspace": SourceDomain.WORKSPACE,
    }
    assert dict(SOURCE_TYPE_TO_DOMAIN) == expected
    for source_type, domain in expected.items():
        assert source_domain_for_type(source_type) is domain
    for source_type in ("dvc", "manual", "", "Git", " git", "workspace ", "unknown"):
        _assert_reason(
            RawV2Reason.SOURCE_ADAPTER_UNREGISTERED,
            source_domain_for_type,
            source_type,
        )


def test_c9_utf8_size_registry_and_reason_policy_are_complete() -> None:
    assert dict(SIZE_LIMITS) == {
        SizeLimit.PORTABLE_ID: 240,
        SizeLimit.SOURCE_OBJECT_ID: 2_000,
        SizeLimit.MEDIA_TYPE: 200,
        SizeLimit.TRACE_MUTATION_ID: 240,
        SizeLimit.REASON_DERIVATION_ADAPTER_SCHEMA: 160,
        SizeLimit.CANONICAL_JSON: 65_536,
        SizeLimit.SELECTED_OUTPUT: 1_048_576,
    }
    assert len(RawV2Reason) == 44
    assert set(REASON_POLICY) == set(RawV2Reason)
    assert REASON_POLICY[RawV2Reason.SELECTOR_OUT_OF_BOUNDS] == ReasonPolicy(
        exception_class=ExceptionClass.INTEGRITY,
        http_status=503,
        audit_severity=AuditSeverity.P0,
        outward_disposition=OutwardDisposition.EVIDENCE_UNAVAILABLE,
    )
    assert REASON_POLICY[RawV2Reason.PARSE_ARTIFACT_MISSING] == ReasonPolicy(
        exception_class=ExceptionClass.UNAVAILABLE,
        http_status=404,
        audit_severity=AuditSeverity.P1,
        outward_disposition=OutwardDisposition.EVIDENCE_UNAVAILABLE,
    )
    assert REASON_POLICY[RawV2Reason.PARSE_ARTIFACT_MISMATCH] == ReasonPolicy(
        exception_class=ExceptionClass.INTEGRITY,
        http_status=503,
        audit_severity=AuditSeverity.P0,
        outward_disposition=OutwardDisposition.EVIDENCE_UNAVAILABLE,
    )
    assert REASON_POLICY[RawV2Reason.UNSAFE_CONTENT] == ReasonPolicy(
        exception_class=ExceptionClass.FORBIDDEN,
        http_status=404,
        audit_severity=AuditSeverity.P0,
        outward_disposition=OutwardDisposition.EVIDENCE_UNAVAILABLE,
    )
    assert REASON_POLICY[RawV2Reason.UNSUPPORTED_MEDIA] == ReasonPolicy(
        exception_class=ExceptionClass.UNAVAILABLE,
        http_status=415,
        audit_severity=AuditSeverity.P1,
        outward_disposition=OutwardDisposition.EVIDENCE_UNAVAILABLE,
    )
    assert set(AUDIT_SEVERITIES) == set(AuditSeverity)
    assert set(OUTWARD_DISPOSITIONS) == set(OutwardDisposition)
    assert set(EXCEPTION_CLASSES) == set(ExceptionClass)
    for limit, byte_limit in SIZE_LIMITS.items():
        ensure_utf8_size("a" * (byte_limit - 1), limit)
        ensure_utf8_size("a" * byte_limit, limit)
        ensure_utf8_size("é" * (byte_limit // 2), limit)
        _assert_reason(
            RawV2Reason.CONTRACT_SIZE_EXCEEDED,
            ensure_utf8_size,
            "a" * (byte_limit + 1),
            limit,
        )
        _assert_reason(
            RawV2Reason.CONTRACT_SIZE_EXCEEDED,
            ensure_utf8_size,
            "é" * (byte_limit // 2 + 1),
            limit,
        )
    with pytest.raises(ValueError):
        RawV2Reason("CANONICALIZATION_MISMATCH")
    error = RawV2ContractError(RawV2Reason.ACL_DENIED)
    assert error.policy.http_status == 404
    assert error.policy.audit_severity is AuditSeverity.P0
    assert error.policy.outward_disposition is OutwardDisposition.EVIDENCE_UNAVAILABLE


def test_c10_fixture_versions_registries_and_identity_literals() -> None:
    fixture = _fixture_payload()
    assert fixture["schema_version"] == "raw-v2-canonical-vectors-v1"
    assert fixture["contract_version"] == RAW_V2_CONTRACT_VERSION
    assert fixture["canonical_json_version"] == CANONICAL_JSON_VERSION
    vectors = fixture["vectors"]
    assert isinstance(vectors, dict)
    canonical = vectors["canonical"]
    assert isinstance(canonical, dict)
    assert canonical_json_bytes(canonical["input"], allow_none=True).decode() == canonical["output"]
    exact_bytes = vectors["exact_bytes"]
    assert isinstance(exact_bytes, dict)
    assert exact_bytes_sha256(bytes.fromhex(exact_bytes["hex"])) == exact_bytes["sha256"]
    timestamps = vectors["timestamps"]
    assert isinstance(timestamps, list)
    for item in timestamps:
        assert isinstance(item, dict)
        assert canonical_timestamp(item["input"]) == item["output"]
    assert vectors["source_mapping"] == {
        key: value.value for key, value in SOURCE_TYPE_TO_DOMAIN.items()
    }
    assert vectors["size_limits"] == {key.value: value for key, value in SIZE_LIMITS.items()}
    assert vectors["reason_policy"] == {
        reason.value: {
            "audit_severity": policy.audit_severity.value,
            "exception_class": policy.exception_class.value,
            "http_status": policy.http_status,
            "outward_disposition": policy.outward_disposition.value,
        }
        for reason, policy in REASON_POLICY.items()
    }
    identities = vectors["identities"]
    assert isinstance(identities, dict)
    assert derive_raw_object_id(_logical_payload()) == identities["raw_object_id"]
    assert derive_raw_object_id(
        _logical_payload(raw_content_sha256=exact_bytes_sha256(b"print('authority')\n"))
    ) == identities["active_raw_object_id"]
    assert derive_source_event_id(_event_payload()) == identities["event_id"]
    assert derive_selector_sha256(_selector_payload()) == identities["selector_sha256"]
    assert derive_locator_id(_binding_payload()) == identities["locator_id"]


def test_c10_copied_fixture_tamper_and_relocated_import(tmp_path: Path) -> None:
    copied_fixture = tmp_path / FIXTURE.name
    shutil.copyfile(FIXTURE, copied_fixture)
    assert _fixture_payload(copied_fixture)["content_sha256"]
    copied_fixture.write_bytes(copied_fixture.read_bytes().replace(b"project-", b"PROJECT-", 1))
    with pytest.raises(AssertionError):
        _fixture_payload(copied_fixture)

    relocated_source = tmp_path / "relocated/src"
    shutil.copytree(
        ROOT / "src/evidence_rag",
        relocated_source / "evidence_rag",
    )
    command = [
        sys.executable,
        "-I",
        "-c",
        (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from evidence_rag.sources.v2.contracts import RAW_V2_CONTRACT_VERSION; "
            "print(RAW_V2_CONTRACT_VERSION)"
        ),
        str(relocated_source),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    assert result.stdout.strip() == RAW_V2_CONTRACT_VERSION


def test_c10_locale_independence() -> None:
    original = locale.setlocale(locale.LC_ALL)
    available = {"C"}
    result = subprocess.run(["locale", "-a"], check=True, capture_output=True, text=True)
    for candidate in result.stdout.splitlines():
        if "utf" in candidate.lower():
            available.add(candidate)
    outputs: set[bytes] = set()
    try:
        for candidate in sorted(available)[:4]:
            try:
                locale.setlocale(locale.LC_ALL, candidate)
            except locale.Error:
                continue
            outputs.add(canonical_json_bytes({"é": "résumé", "z": 1}))
    finally:
        locale.setlocale(locale.LC_ALL, original)
    assert outputs == {'{"z":1,"é":"résumé"}'.encode()}


def test_c10_built_wheel_contains_and_imports_namespace_module(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        [
            uv,
            "build",
            "--wheel",
            "--no-build-isolation",
            "--out-dir",
            str(wheelhouse),
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = tuple(wheelhouse.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        assert "evidence_rag/sources/v2/contracts.py" in names
        extracted = tmp_path / "installed"
        archive.extractall(extracted)
    command = [
        sys.executable,
        "-I",
        "-c",
        (
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from evidence_rag.sources.v2.contracts import RAW_V2_CONTRACT_VERSION; "
            "print(RAW_V2_CONTRACT_VERSION)"
        ),
        str(extracted),
    ]
    imported = subprocess.run(command, check=True, capture_output=True, text=True)
    assert imported.stdout.strip() == RAW_V2_CONTRACT_VERSION


def test_contract_module_has_no_operational_side_effect_inputs() -> None:
    source = (ROOT / "src/evidence_rag/sources/v2/contracts.py").read_text()
    forbidden = (
        "sqlite3",
        "requests",
        "httpx",
        "os.environ",
        "Path(",
        "open(",
        "uuid",
        "random",
        "datetime.now",
        "time.time",
    )
    assert [token for token in forbidden if token in source] == []
    assert validate_sha256_digest(exact_bytes_sha256(b"contract"))
