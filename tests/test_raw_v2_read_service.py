from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import sqlite3
import stat
from dataclasses import FrozenInstanceError, dataclass, fields, replace
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

import evidence_rag.sources.v2.audit as audit_module
import evidence_rag.sources.v2.read_service as read_service_module
from evidence_rag.sources.v2.audit import (
    RAW_V2_READ_AUDIT_VERSION,
    RawV2ReadAuditRecord,
    RawV2ReadOutcome,
)
from evidence_rag.sources.v2.contracts import (
    AuditSeverity,
    ExceptionClass,
    OutwardDisposition,
    RawV2ContractError,
    RawV2Reason,
    ReasonPolicy,
    SourceDomain,
    canonical_json_bytes,
    exact_bytes_sha256,
)
from evidence_rag.sources.v2.models import RawV2SelectedReadPublicResponse
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.read_service import (
    RAW_V2_READ_SERVICE_VERSION,
    RawEvidenceReadRequestV2,
    RawEvidenceReadResultV2,
    RawV2ReadOutputPolicy,
    RawV2SelectedReadService,
)
from evidence_rag.sources.v2.reader import RawManagedReadRequest, RawV2ManagedReader
from evidence_rag.sources.v2.router import RawV2PublicProjectionRouter, RawV2PublicResult
from evidence_rag.sources.v2.schema import initialize_raw_v2_schema
from evidence_rag.sources.v2.selector import RawSelectedReadResult, RawV2SelectorExecutor
from evidence_rag.sources.v2.store import (
    RawBindingRecord,
    RawBindingWriteRequest,
    RawObjectWriteRequest,
    RawV2BindingStore,
    RawV2ObjectStore,
)

NOW = "2026-09-01T00:00:00.000000Z"
LATER = "2026-09-01T00:00:01.000000Z"
OBSERVED = "2026-08-31T23:59:00.000000Z"
PURPOSE = "grounded_answer"
TRACE = "trace-read-001"
AUDIT_KEY = b"reviewed-test-audit-key-32-byte!"
PEER_AUDIT_KEY = b"peer-review-audit-key-exact-32!!"
PUBLIC_FIELD_SET_SHA256 = "sha256:79f52dff44534b8f2cb69e953dc540be7c243eb8e5433fe41c67350637f64757"
AUDIT_FIELD_SET_SHA256 = "sha256:9adeb3b8c1ab5c96bc3ab3ad5273cdcb056d3a9bb6979096472d7020cc3761f3"


class _Authority:
    def project_exists(self, *, project_id: str) -> bool:
        return project_id == "project-a"

    def resolve_object_acl_ref(
        self,
        *,
        project_id: str,
        source_acl_ref: str,
    ) -> str | None:
        if project_id != "project-a":
            return None
        if source_acl_ref == "source-public":
            return "public"
        if source_acl_ref == "source-private":
            return "team-a"
        return None

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        if project_id != "project-a" or principal_id == "unknown-principal":
            return None
        if principal_id == "private-reader":
            return ("team-a",)
        return ()


def _project_policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


@dataclass(slots=True)
class _Fixture:
    connection: sqlite3.Connection
    root: Path
    payload: bytes
    binding: RawBindingRecord
    managed_request: RawManagedReadRequest
    request: RawEvidenceReadRequestV2
    reader: RawV2ManagedReader
    executor: RawV2SelectorExecutor


def _fixture(
    tmp_path: Path,
    *,
    payload: bytes = b"reviewed selected evidence\n",
    private: bool = False,
) -> _Fixture:
    connection = sqlite3.connect(":memory:")
    initialize_raw_v2_schema(connection)
    root = tmp_path / "blobs"
    root.mkdir(parents=True, exist_ok=True)
    source_acl = "source-private" if private else "source-public"
    raw = RawV2ObjectStore(
        connection=connection,
        blob_root=root,
        policy=_project_policy(),
        clock=lambda: NOW,
    ).persist_bytes(
        request=RawObjectWriteRequest(
            project_id="project-a",
            source_type="git",
            source_instance_id="repository-1",
            source_object_id="src/app.py",
            source_version="commit-1",
            stable_version="blob-1",
            source_acl_ref=source_acl,
            media_type="application/octet-stream",
            adapter_version="git-adapter-v2",
            schema_version="raw-object-v2",
            metadata_projection={},
            observed_at=OBSERVED,
        ),
        payload=payload,
    )
    binding = RawV2BindingStore(
        connection=connection,
        policy=_project_policy(),
        clock=lambda: NOW,
    ).persist(
        request=RawBindingWriteRequest(
            project_id="project-a",
            raw_object_id=raw.raw_object_id,
            source_acl_ref=source_acl,
            source_version="commit-1",
            stable_version="blob-1",
            generation_id="generation-1",
            derived_entity_id="entity-1",
            retrieval_unit_id="unit-1",
            derivation_kind="source_slice",
            derivation_version="derivation-v1",
            selector_kind="whole_object_v2",
            selector={},
            parser_artifact_sha256=None,
            valid_from="2026-08-31T23:58:00Z",
            valid_to=None,
            observed_at=OBSERVED,
            adapter_version="git-adapter-v2",
            schema_version="raw-binding-v2",
        ),
        selected_content=payload,
    )
    managed_request = RawManagedReadRequest(
        project_id="project-a",
        principal_id="private-reader" if private else "public-reader",
        locator_id=binding.locator_id,
        expected_binding_sha256=binding.binding_sha256,
        expected_source_domain=SourceDomain.CODE,
        expected_source_type="git",
        expected_source_instance_id="repository-1",
        expected_source_object_id="src/app.py",
        expected_source_version="commit-1",
        expected_stable_version="blob-1",
        expected_generation_id="generation-1",
    )
    request = RawEvidenceReadRequestV2(
        **{field.name: getattr(managed_request, field.name) for field in fields(managed_request)},
        purpose=PURPOSE,
        trace_id=TRACE,
    )
    return _Fixture(
        connection=connection,
        root=root,
        payload=payload,
        binding=binding,
        managed_request=managed_request,
        request=request,
        reader=RawV2ManagedReader(
            connection=connection,
            blob_root=root,
            policy=_project_policy(),
            max_raw_bytes=max(1, len(payload) + 1),
        ),
        executor=RawV2SelectorExecutor(max_selected_bytes=max(1, len(payload) + 1)),
    )


def _output_policy(
    *,
    allowed_purposes: tuple[str, ...] = (PURPOSE, "wiki_compilation"),
    max_public_bytes: int = 1_048_576,
    forbidden_byte_markers: tuple[bytes, ...] = (b"PRIVATE-KEY", b"SECRET"),
) -> RawV2ReadOutputPolicy:
    return RawV2ReadOutputPolicy(
        allowed_purposes=allowed_purposes,
        max_public_bytes=max_public_bytes,
        forbidden_byte_markers=forbidden_byte_markers,
    )


def _service(
    fixture: _Fixture,
    *,
    output_policy: RawV2ReadOutputPolicy | None = None,
    audit_key: bytes = AUDIT_KEY,
    now: str = NOW,
) -> RawV2SelectedReadService:
    return RawV2SelectedReadService(
        reader=fixture.reader,
        selector_executor=fixture.executor,
        output_policy=output_policy or _output_policy(),
        audit_hmac_key=audit_key,
        clock=lambda: now,
    )


def _database_dump(connection: sqlite3.Connection) -> str:
    return "\n".join(connection.iterdump())


def _tree_projection(root: Path) -> tuple[tuple[object, ...], ...]:
    projection: list[tuple[object, ...]] = []
    for directory, names, files_in_directory in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        for name in sorted([*names, *files_in_directory]):
            item = parent / name
            relative = item.relative_to(root).as_posix()
            metadata = item.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                projection.append(("directory", relative))
            elif stat.S_ISREG(metadata.st_mode):
                raw = item.read_bytes()
                projection.append(("file", relative, len(raw), exact_bytes_sha256(raw)))
            else:
                projection.append(("other", relative, stat.S_IFMT(metadata.st_mode)))
    return tuple(sorted(projection))


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _field_set_sha256(names: list[str]) -> str:
    return _sha256(
        json.dumps(
            sorted(names), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    )


def _body(result: RawV2PublicResult) -> dict[str, Any]:
    value = json.loads(result.body_bytes())
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _assert_no_sensitive_names(value: object, *, public: bool) -> None:
    forbidden_exact = {
        "acl_ref",
        "audit_severity",
        "exception_detail",
        "metadata",
        "metadata_json",
        "parser_artifact_sha256",
        "principal_id",
        "project_id",
        "reason",
        "requester_acl_refs",
        "selector_json",
        "source_instance_id",
        "source_object_id",
        "storage_key",
        "storage_path",
        "trace_id",
        "visibility_partition_sha256",
    }
    if not public:
        forbidden_exact -= {"audit_severity", "reason"}
    if isinstance(value, dict):
        for key, item in value.items():
            assert key not in forbidden_exact
            assert not any(token in key.lower() for token in ("path", "uri", "payload_ref"))
            _assert_no_sensitive_names(item, public=public)
    elif isinstance(value, list):
        for item in value:
            _assert_no_sensitive_names(item, public=public)


def test_read_service_api_and_exact_field_sets_are_frozen(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = _service(fixture)
    result = service.read_evidence_v2(request=fixture.request)

    assert RAW_V2_READ_AUDIT_VERSION == "raw-v2-read-audit-v1"
    assert RAW_V2_READ_SERVICE_VERSION == "raw-v2-selected-read-service-v1"
    assert [field.name for field in fields(RawEvidenceReadRequestV2)] == [
        "project_id",
        "principal_id",
        "locator_id",
        "expected_binding_sha256",
        "expected_source_domain",
        "expected_source_type",
        "expected_source_instance_id",
        "expected_source_object_id",
        "expected_source_version",
        "expected_stable_version",
        "expected_generation_id",
        "purpose",
        "trace_id",
    ]
    assert [field.name for field in fields(RawV2ReadOutputPolicy)] == [
        "allowed_purposes",
        "max_public_bytes",
        "forbidden_byte_markers",
    ]
    assert [field.name for field in fields(RawEvidenceReadResultV2)] == [
        "selected",
        "public",
        "audit",
    ]
    assert [field.name for field in fields(RawV2ReadAuditRecord)] == [
        "schema_version",
        "audit_sha256",
        "occurred_at",
        "outcome",
        "purpose",
        "trace_token_sha256",
        "request_scope_token_sha256",
        "public_status_code",
        "public_disposition",
        "source_domain",
        "reason",
        "audit_severity",
        "selected_receipt_sha256",
    ]
    assert _field_set_sha256(list(RawV2SelectedReadPublicResponse.model_fields)) == (
        PUBLIC_FIELD_SET_SHA256
    )
    assert _field_set_sha256([field.name for field in fields(RawV2ReadAuditRecord)]) == (
        AUDIT_FIELD_SET_SHA256
    )
    with pytest.raises(FrozenInstanceError):
        fixture.request.trace_id = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.audit.purpose = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.selected = None  # type: ignore[misc]


def test_safe_selected_read_binds_exact_public_bytes_and_audit(tmp_path: Path) -> None:
    payload = b"\x00safe reviewed evidence\n\xff"
    fixture = _fixture(tmp_path, payload=payload)
    before_database = _database_dump(fixture.connection)
    before_tree = _tree_projection(fixture.root)

    result = _service(fixture).read_evidence_v2(request=fixture.request)

    assert type(result) is RawEvidenceReadResultV2
    assert type(result.selected) is RawSelectedReadResult
    assert result.selected.selected_bytes == payload
    assert type(result.public) is RawV2PublicResult
    assert result.public.status_code == 200
    assert type(result.public.body) is RawV2SelectedReadPublicResponse
    body = _body(result.public)
    assert body == {
        "audit_sha256": result.audit.audit_sha256,
        "binding_sha256": fixture.binding.binding_sha256,
        "evidence_state": "OBSERVED",
        "generation_id": "generation-1",
        "locator_id": fixture.binding.locator_id,
        "media_type": "application/octet-stream",
        "raw_content_sha256": exact_bytes_sha256(payload),
        "raw_object_id": result.selected.raw_object_id,
        "selected_base64": base64.b64encode(payload).decode("ascii"),
        "selected_byte_length": len(payload),
        "selected_content_sha256": exact_bytes_sha256(payload),
        "selector_kind": "whole_object_v2",
        "selector_sha256": result.selected.selector_sha256,
        "source_domain": "code",
        "source_type": "git",
        "source_version": "commit-1",
        "stable_version": "blob-1",
    }
    assert result.audit.schema_version == RAW_V2_READ_AUDIT_VERSION
    assert result.audit.outcome is RawV2ReadOutcome.OBSERVED
    assert result.audit.reason is None
    assert result.audit.audit_severity is None
    assert result.audit.public_status_code == 200
    assert result.audit.public_disposition is None
    assert result.audit.selected_receipt_sha256 is not None
    assert result.audit.verify() is result.audit
    assert result.audit.audit_sha256 == result.public.body.audit_sha256
    assert _database_dump(fixture.connection) == before_database
    assert _tree_projection(fixture.root) == before_tree
    _assert_no_sensitive_names(body, public=True)


def test_composite_result_rejects_cross_wired_selected_public_and_audit(tmp_path: Path) -> None:
    first = _fixture(tmp_path / "first", payload=b"first evidence")
    second = _fixture(tmp_path / "second", payload=b"second evidence")
    first_result = _service(first).read_evidence_v2(request=first.request)
    second_result = _service(second).read_evidence_v2(request=second.request)
    assert first_result.selected is not None
    with pytest.raises(ValueError, match="selected receipt differs"):
        RawEvidenceReadResultV2(
            selected=first_result.selected,
            public=second_result.public,
            audit=second_result.audit,
        )
    with pytest.raises(ValueError, match="not bound to its audit"):
        RawEvidenceReadResultV2(
            selected=first_result.selected,
            public=first_result.public,
            audit=second_result.audit,
        )


def test_public_selected_model_rejects_noncanonical_or_inconsistent_base64(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, payload=b"a")
    body = _service(fixture).read_evidence_v2(request=fixture.request).public.body
    assert type(body) is RawV2SelectedReadPublicResponse
    with pytest.raises(ValidationError):
        body.model_copy(update={"selected_base64": "YQ"})
    with pytest.raises(ValidationError):
        body.model_copy(update={"selected_base64": "Yg=="})
    with pytest.raises(ValidationError):
        body.model_copy(update={"selected_byte_length": 2})
    with pytest.raises(ValidationError):
        RawV2SelectedReadPublicResponse.model_validate(
            {**body.model_dump(mode="python"), "storage_path": "/secret"}
        )
    with pytest.raises(ValidationError):
        body.audit_sha256 = "sha256:" + "0" * 64  # type: ignore[misc]


def test_policy_is_canonical_and_denied_purpose_stops_before_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    calls = 0

    def forbidden_read(*args: object, **kwargs: object) -> RawSelectedReadResult:
        nonlocal calls
        calls += 1
        raise AssertionError("reader must not be called for denied purpose")

    monkeypatch.setattr(RawV2ManagedReader, "read_selected", forbidden_read)
    service = _service(
        fixture,
        output_policy=_output_policy(allowed_purposes=("wiki_compilation",)),
    )
    result = service.read_evidence_v2(request=fixture.request)
    assert calls == 0
    assert result.selected is None
    assert result.audit.reason is RawV2Reason.ACL_DENIED
    assert result.audit.audit_severity is AuditSeverity.P0
    assert result.audit.outcome is RawV2ReadOutcome.REJECTED
    assert result.public.status_code == 404
    assert result.public.body_bytes() == b'{"code":"evidence_unavailable"}'

    for invalid_request in (
        {"purpose": ""},
        {"purpose": "grounded_e\u0301vidence"},
        {"trace_id": "trace\ncontrol"},
        {"trace_id": "t" * 241},
    ):
        with pytest.raises(RawV2ContractError):
            replace(fixture.request, **invalid_request)
    with pytest.raises(TypeError, match="at least 32 bytes"):
        _service(fixture, audit_key=b"k" * 31)

    for invalid in (
        {"allowed_purposes": ("z", "a")},
        {"allowed_purposes": (PURPOSE, PURPOSE)},
        {"forbidden_byte_markers": (b"",)},
        {"forbidden_byte_markers": (b"z", b"a")},
        {"max_public_bytes": 0},
        {"max_public_bytes": 1_048_577},
    ):
        values: dict[str, object] = {
            "allowed_purposes": (PURPOSE,),
            "max_public_bytes": 10,
            "forbidden_byte_markers": (b"SECRET",),
        }
        values.update(invalid)
        with pytest.raises(RawV2ContractError):
            RawV2ReadOutputPolicy(**values)  # type: ignore[arg-type]


def test_public_output_ceiling_is_exact_and_never_truncates(tmp_path: Path) -> None:
    at_limit = _fixture(tmp_path / "at", payload=b"abcd")
    above_limit = _fixture(tmp_path / "above", payload=b"abcde")
    policy = _output_policy(max_public_bytes=4)

    passed = _service(at_limit, output_policy=policy).read_evidence_v2(request=at_limit.request)
    rejected = _service(above_limit, output_policy=policy).read_evidence_v2(
        request=above_limit.request
    )

    assert passed.selected is not None and passed.selected.selected_bytes == b"abcd"
    assert _body(passed.public)["selected_base64"] == "YWJjZA=="
    assert rejected.selected is None
    assert rejected.audit.reason is RawV2Reason.CONTRACT_SIZE_EXCEEDED
    assert rejected.public.status_code == 413
    assert _body(rejected.public) == {"code": "invalid_request"}


@pytest.mark.parametrize(
    "payload",
    [b"SECRET-safe-tail", b"safe-SECRET-tail", b"safe-tail-SECRET"],
)
def test_full_marker_scan_rejects_beginning_middle_and_end(
    tmp_path: Path,
    payload: bytes,
) -> None:
    fixture = _fixture(tmp_path, payload=payload)
    before_database = _database_dump(fixture.connection)
    before_tree = _tree_projection(fixture.root)

    result = _service(fixture).read_evidence_v2(request=fixture.request)

    assert result.selected is None
    assert result.audit.reason is RawV2Reason.UNSAFE_CONTENT
    assert result.audit.audit_severity is AuditSeverity.P0
    assert result.public.status_code == 404
    assert result.public.body_bytes() == b'{"code":"evidence_unavailable"}'
    assert _database_dump(fixture.connection) == before_database
    assert _tree_projection(fixture.root) == before_tree


def test_marker_scan_is_binary_exact_and_near_miss_passes(tmp_path: Path) -> None:
    payload = b"SECRE\x00T\xffPRIVATE-KE"
    fixture = _fixture(tmp_path, payload=payload)
    result = _service(fixture).read_evidence_v2(request=fixture.request)
    assert result.selected is not None
    assert result.selected.selected_bytes == payload
    assert base64.b64decode(_body(result.public)["selected_base64"], validate=True) == payload


@pytest.mark.parametrize(
    "reason",
    [
        RawV2Reason.PROJECT_MISMATCH,
        RawV2Reason.ACL_DENIED,
        RawV2Reason.VISIBILITY_MISMATCH,
        RawV2Reason.BINDING_NOT_FOUND,
        RawV2Reason.RAW_NOT_FOUND,
        RawV2Reason.RAW_QUARANTINED,
        RawV2Reason.RAW_TOMBSTONED,
        RawV2Reason.UNSAFE_CONTENT,
    ],
)
def test_hidden_existence_and_unsafe_failures_have_byte_identical_public_bodies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: RawV2Reason,
) -> None:
    fixture = _fixture(tmp_path)
    detail = f"never expose project-a/src/app.py/{reason.value}/SECRET"

    def fail(*args: object, **kwargs: object) -> RawSelectedReadResult:
        raise RawV2ContractError(reason, detail)

    monkeypatch.setattr(RawV2ManagedReader, "read_selected", fail)
    result = _service(fixture).read_evidence_v2(request=fixture.request)
    assert result.selected is None
    assert result.public.status_code == 404
    assert result.public.body_bytes() == b'{"code":"evidence_unavailable"}'
    assert result.audit.reason is reason
    assert result.audit.audit_severity is RawV2ContractError(reason).policy.audit_severity
    audit_bytes = result.audit.canonical_bytes()
    for sensitive in (b"project-a", b"src/app.py", b"SECRET", detail.encode()):
        assert sensitive not in audit_bytes


@pytest.mark.parametrize(
    "reason,status,code",
    [
        (RawV2Reason.SELECTOR_INVALID, 422, "invalid_request"),
        (RawV2Reason.UNSUPPORTED_MEDIA, 415, "evidence_unavailable"),
        (RawV2Reason.SELECTED_DIGEST_MISMATCH, 503, "evidence_unavailable"),
    ],
)
def test_other_failures_use_registry_mapping_without_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: RawV2Reason,
    status: int,
    code: str,
) -> None:
    fixture = _fixture(tmp_path)
    detail = "SECRET at /private/storage/path"

    def fail(*args: object, **kwargs: object) -> RawSelectedReadResult:
        raise RawV2ContractError(reason, detail)

    monkeypatch.setattr(RawV2ManagedReader, "read_selected", fail)
    result = _service(fixture).read_evidence_v2(request=fixture.request)
    assert result.public.status_code == status
    assert _body(result.public) == {"code": code}
    assert result.audit.reason is reason
    assert detail.encode() not in result.audit.canonical_bytes()


def test_audit_hmac_tokens_separate_trace_scope_key_and_time(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    same_a = _service(fixture).read_evidence_v2(request=fixture.request).audit
    same_b = _service(fixture).read_evidence_v2(request=fixture.request).audit
    changed_trace = (
        _service(fixture)
        .read_evidence_v2(request=replace(fixture.request, trace_id="trace-read-002"))
        .audit
    )
    changed_scope = (
        _service(fixture)
        .read_evidence_v2(request=replace(fixture.request, principal_id="public-reader-2"))
        .audit
    )
    changed_key = (
        _service(fixture, audit_key=PEER_AUDIT_KEY).read_evidence_v2(request=fixture.request).audit
    )
    changed_time = _service(fixture, now=LATER).read_evidence_v2(request=fixture.request).audit

    assert same_a == same_b
    assert same_a.trace_token_sha256 == changed_scope.trace_token_sha256
    assert same_a.request_scope_token_sha256 != changed_scope.request_scope_token_sha256
    assert same_a.trace_token_sha256 != changed_trace.trace_token_sha256
    assert same_a.request_scope_token_sha256 == changed_trace.request_scope_token_sha256
    assert same_a.trace_token_sha256 != changed_key.trace_token_sha256
    assert same_a.request_scope_token_sha256 != changed_key.request_scope_token_sha256
    assert same_a.trace_token_sha256 == changed_time.trace_token_sha256
    assert same_a.request_scope_token_sha256 == changed_time.request_scope_token_sha256
    assert same_a.audit_sha256 != changed_time.audit_sha256
    assert same_a.selected_receipt_sha256 == changed_time.selected_receipt_sha256


def test_audit_contains_only_pseudonyms_and_controlled_failure_fields(tmp_path: Path) -> None:
    payload = b"safe selected bytes"
    fixture = _fixture(tmp_path, payload=payload)
    success = _service(fixture).read_evidence_v2(request=fixture.request)
    failure = _service(
        fixture,
        output_policy=_output_policy(forbidden_byte_markers=(b"selected",)),
    ).read_evidence_v2(request=fixture.request)

    for audit in (success.audit, failure.audit):
        assert audit.verify() is audit
        projected = audit.canonical_value()
        _assert_no_sensitive_names(projected, public=False)
        raw = audit.canonical_bytes()
        for sensitive in (
            fixture.request.project_id.encode(),
            fixture.request.principal_id.encode(),
            fixture.request.locator_id.encode(),
            fixture.request.expected_binding_sha256.encode(),
            fixture.request.expected_source_instance_id.encode(),
            fixture.request.expected_source_object_id.encode(),
            fixture.request.trace_id.encode(),
            payload,
            base64.b64encode(payload),
            AUDIT_KEY,
        ):
            assert sensitive not in raw
    assert success.audit.reason is None
    assert failure.audit.reason is RawV2Reason.UNSAFE_CONTENT
    assert failure.audit.selected_receipt_sha256 is None


def test_router_accepts_only_already_safe_selected_public_model(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _service(fixture).read_evidence_v2(request=fixture.request)
    body = result.public.body
    assert type(body) is RawV2SelectedReadPublicResponse
    projected = RawV2PublicProjectionRouter().selected_read(response=body)
    assert projected.status_code == 200
    assert projected.body is body
    with pytest.raises(TypeError):
        RawV2PublicProjectionRouter().selected_read(  # type: ignore[arg-type]
            response=body.model_dump(mode="python")
        )
    assert result.selected is not None
    with pytest.raises(TypeError):
        RawV2PublicProjectionRouter().selected_read(response=result.selected)  # type: ignore[arg-type]


def test_service_does_not_mislabel_programmer_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)

    def fail(*args: object, **kwargs: object) -> RawSelectedReadResult:
        raise RuntimeError("programmer failure")

    monkeypatch.setattr(RawV2ManagedReader, "read_selected", fail)
    with pytest.raises(RuntimeError, match="programmer failure"):
        _service(fixture).read_evidence_v2(request=fixture.request)
    with pytest.raises(TypeError):
        _service(fixture).read_evidence_v2(request=cast(Any, {"project_id": "project-a"}))


def test_new_modules_have_no_persistence_runtime_or_fallback_capability() -> None:
    banned_imports = {
        "asyncio",
        "http",
        "os",
        "pathlib",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
    }
    banned_calls = {"compile", "eval", "exec", "open"}
    for module in (audit_module, read_service_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not (imports & banned_imports)
        assert not (calls & banned_calls)
        assert "fallback" not in source.lower()


def test_unsafe_content_reason_policy_is_exact() -> None:
    assert len(RawV2Reason) == 44
    assert RawV2Reason.UNSAFE_CONTENT.value == "unsafe_content"
    assert list(RawV2Reason).index(RawV2Reason.UNSAFE_CONTENT) + 1 == list(RawV2Reason).index(
        RawV2Reason.UNSUPPORTED_MEDIA
    )
    error = RawV2ContractError(RawV2Reason.UNSAFE_CONTENT)
    assert error.policy == ReasonPolicy(
        exception_class=ExceptionClass.FORBIDDEN,
        http_status=404,
        audit_severity=AuditSeverity.P0,
        outward_disposition=OutwardDisposition.EVIDENCE_UNAVAILABLE,
    )
    assert canonical_json_bytes({"code": error.policy.outward_disposition.value}) == (
        b'{"code":"evidence_unavailable"}'
    )
