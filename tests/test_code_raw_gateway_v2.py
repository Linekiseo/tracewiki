from __future__ import annotations

import ast
import base64
import hashlib
import inspect
import json
import sqlite3
from dataclasses import FrozenInstanceError, dataclass, fields, replace
from pathlib import Path

import pytest

import evidence_rag.rag.sources.code as code_api
import evidence_rag.rag.sources.code.raw_gateway_v2 as gateway_module
from evidence_rag.parser import CodeParser
from evidence_rag.rag.sources.code.raw_gateway_v2 import (
    CODE_RAW_GATEWAY_V2_VERSION,
    CodeRawBindingRequestV2,
    CodeRawGatewayV2,
    CodeRawReadRequestV2,
)
from evidence_rag.rag.sources.code.unit_builder import (
    CodeSourceLineage,
    CodeUnitBuilder,
    CodeUnitBuildRequest,
    CodeUnitRecord,
)
from evidence_rag.sources.v2.audit import RawV2ReadOutcome
from evidence_rag.sources.v2.contracts import (
    RawV2ContractError,
    RawV2Reason,
    SourceDomain,
    exact_bytes_sha256,
)
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.read_service import (
    RawEvidenceReadResultV2,
    RawV2ReadOutputPolicy,
    RawV2SelectedReadService,
)
from evidence_rag.sources.v2.reader import RawV2ManagedReader
from evidence_rag.sources.v2.schema import initialize_raw_v2_schema
from evidence_rag.sources.v2.selector import RawV2SelectorExecutor
from evidence_rag.sources.v2.store import (
    RawBindingRecord,
    RawObjectWriteRequest,
    RawTombstoneRequest,
    RawV2BindingStore,
    RawV2ObjectStore,
    RawV2TombstoneStore,
)

NOW = "2026-09-01T00:00:00.000000Z"
OBSERVED = "2026-08-31T23:59:00.000000Z"
PROJECT = "project-a"
REPOSITORY = "repository-1"
GENERATION = "generation-1"
ENTITY = "entity-file-1"
PATH = "src/greeting.py"
REF = "commit-0123456789abcdef"
BLOB = "blob-sha256-0123456789abcdef"
ACL = "source-public"
PURPOSE = "grounded_answer"
TRACE = "trace-code-gateway-001"
AUDIT_KEY = b"reviewed-code-gateway-audit-key-32!"
SOURCE = (
    'def greet(name):\r\n    prefix = "你好"\r\n'
    '    return f"{prefix}, {name}"\r\n\r\n'
    "answer = greet('Codex')\r\n"
)


class _Authority:
    def project_exists(self, *, project_id: str) -> bool:
        return project_id in {"project-a", "project-b"}

    def resolve_object_acl_ref(
        self,
        *,
        project_id: str,
        source_acl_ref: str,
    ) -> str | None:
        if project_id not in {"project-a", "project-b"}:
            return None
        if source_acl_ref == "source-public":
            return "public"
        if source_acl_ref == "source-private":
            return f"team-{project_id[-1]}"
        return None

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        if project_id not in {"project-a", "project-b"}:
            return None
        if principal_id == "denied-reader":
            return None
        if principal_id == "private-reader":
            return (f"team-{project_id[-1]}",)
        return ()


def _policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


@dataclass(slots=True)
class _Fixture:
    connection: sqlite3.Connection
    root: Path
    payload: bytes
    raw_object_id: str
    unit: CodeUnitRecord
    binding_store: RawV2BindingStore
    gateway: CodeRawGatewayV2


def _fixture(
    tmp_path: Path,
    *,
    source_acl_ref: str = ACL,
) -> _Fixture:
    connection = sqlite3.connect(":memory:")
    initialize_raw_v2_schema(connection)
    root = tmp_path / "blobs"
    root.mkdir(parents=True)
    policy = _policy()
    payload = SOURCE.encode("utf-8")
    raw = RawV2ObjectStore(
        connection=connection,
        blob_root=root,
        policy=policy,
        clock=lambda: NOW,
    ).persist_bytes(
        request=RawObjectWriteRequest(
            project_id=PROJECT,
            source_type="git",
            source_instance_id=REPOSITORY,
            source_object_id=PATH,
            source_version=REF,
            stable_version=BLOB,
            source_acl_ref=source_acl_ref,
            media_type="text/x-python; charset=utf-8",
            adapter_version="git-adapter-v2",
            schema_version="raw-object-v2",
            metadata_projection={},
            observed_at=OBSERVED,
        ),
        payload=payload,
    )
    parsed = CodeParser().parse(PATH, SOURCE, blob_hash=BLOB)
    built = CodeUnitBuilder().build(
        CodeUnitBuildRequest.from_parsed_file(
            parsed,
            project_id=PROJECT,
            repository_id=REPOSITORY,
            generation_id=GENERATION,
            entity_id=ENTITY,
            ref=REF,
            acl_ref=source_acl_ref,
            qualified_name="greeting.greet",
            lineage_attributes={
                "entity_id": ENTITY,
                "generation_id": GENERATION,
                "raw_object_id": raw.raw_object_id,
                "snapshot_dirty": "false",
                "snapshot_head_commit": REF,
            },
        )
    )
    unit = next(record for record in built.records if record.ast_node_type == "function_definition")
    binding_store = RawV2BindingStore(
        connection=connection,
        policy=policy,
        clock=lambda: NOW,
    )
    service = RawV2SelectedReadService(
        reader=RawV2ManagedReader(
            connection=connection,
            blob_root=root,
            policy=policy,
            max_raw_bytes=len(payload) + 1,
        ),
        selector_executor=RawV2SelectorExecutor(max_selected_bytes=len(payload) + 1),
        output_policy=RawV2ReadOutputPolicy(
            allowed_purposes=(PURPOSE, "wiki_compilation"),
            max_public_bytes=len(payload) + 1,
            forbidden_byte_markers=(b"PRIVATE-KEY", b"SECRET"),
        ),
        audit_hmac_key=AUDIT_KEY,
        clock=lambda: NOW,
    )
    return _Fixture(
        connection=connection,
        root=root,
        payload=payload,
        raw_object_id=raw.raw_object_id,
        unit=unit,
        binding_store=binding_store,
        gateway=CodeRawGatewayV2(
            policy=policy,
            binding_store=binding_store,
            read_service=service,
        ),
    )


def _bind(fixture: _Fixture, *, unit: CodeUnitRecord | None = None) -> RawBindingRecord:
    return fixture.gateway.bind_unit(
        request=CodeRawBindingRequestV2(
            unit=unit or fixture.unit,
            raw_file_bytes=fixture.payload,
            observed_at=OBSERVED,
        )
    )


def _read_request(
    fixture: _Fixture,
    binding: RawBindingRecord,
    **updates: object,
) -> CodeRawReadRequestV2:
    values: dict[str, object] = {
        "unit": fixture.unit,
        "binding": binding,
        "project_id": PROJECT,
        "repository_id": REPOSITORY,
        "file_path": PATH,
        "ref": REF,
        "blob_hash": BLOB,
        "generation_id": GENERATION,
        "principal_id": "public-reader",
        "purpose": PURPOSE,
        "trace_id": TRACE,
    }
    values.update(updates)
    return CodeRawReadRequestV2(**values)  # type: ignore[arg-type]


def _lineage_attributes(unit: CodeUnitRecord, **updates: str) -> CodeUnitRecord:
    attributes = dict(unit.source_lineage.attributes)
    attributes.update(updates)
    return replace(
        unit,
        source_lineage=replace(
            unit.source_lineage,
            attributes=tuple(attributes.items()),
        ),
    )


def _unit_span(unit: CodeUnitRecord, **updates: int) -> CodeUnitRecord:
    span = replace(unit.span, **updates)
    return replace(unit, span=span, context_ref=replace(unit.context_ref, span=span))


def _binding_count(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT COUNT(*) FROM raw_evidence_bindings_v2").fetchone()
    assert row is not None
    return int(row[0])


def _assert_reason(
    error: pytest.ExceptionInfo[RawV2ContractError],
    reason: RawV2Reason,
) -> None:
    assert type(error.value) is RawV2ContractError
    assert error.value.reason is reason


def test_contract_surface_is_frozen_lazy_exported_and_immutable(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    binding_request = CodeRawBindingRequestV2(
        unit=fixture.unit,
        raw_file_bytes=fixture.payload,
        observed_at=OBSERVED,
    )

    assert CODE_RAW_GATEWAY_V2_VERSION == "code-raw-gateway-v2"
    assert {field.name for field in fields(CodeRawBindingRequestV2)} == {
        "unit",
        "raw_file_bytes",
        "observed_at",
    }
    assert {field.name for field in fields(CodeRawReadRequestV2)} == {
        "unit",
        "binding",
        "project_id",
        "repository_id",
        "file_path",
        "ref",
        "blob_hash",
        "generation_id",
        "principal_id",
        "purpose",
        "trace_id",
    }
    assert inspect.signature(CodeRawGatewayV2).parameters.keys() == {
        "policy",
        "binding_store",
        "read_service",
    }
    assert code_api.CodeRawGatewayV2 is CodeRawGatewayV2
    assert code_api.CodeRawBindingRequestV2 is CodeRawBindingRequestV2
    assert code_api.CodeRawReadRequestV2 is CodeRawReadRequestV2
    assert code_api.CODE_RAW_GATEWAY_V2_VERSION == CODE_RAW_GATEWAY_V2_VERSION
    with pytest.raises(FrozenInstanceError):
        binding_request.observed_at = NOW  # type: ignore[misc]
    with pytest.raises(RawV2ContractError) as error:
        fixture.gateway.bind_unit(request={})  # type: ignore[arg-type]
    _assert_reason(error, RawV2Reason.CANONICALIZATION_MISMATCH)


def test_unicode_crlf_unit_binds_and_reads_exact_selected_evidence(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    binding = _bind(fixture)
    result = fixture.gateway.read_unit(request=_read_request(fixture, binding))

    expected = fixture.payload[fixture.unit.span.start_byte : fixture.unit.span.end_byte]
    assert expected == fixture.unit.body.encode("utf-8")
    assert type(result) is RawEvidenceReadResultV2
    assert result.selected is not None
    assert result.selected.selected_bytes == expected
    assert result.selected.source_domain is SourceDomain.CODE
    assert result.selected.source_type == "git"
    assert result.selected.source_instance_id == REPOSITORY
    assert result.selected.source_object_id == PATH
    assert result.selected.source_version == REF
    assert result.selected.stable_version == BLOB
    assert result.selected.generation_id == GENERATION
    assert result.selected.selector_kind == "utf8_range_v2"
    assert result.public.status_code == 200
    assert result.public.body.selected_base64 == base64.b64encode(expected).decode("ascii")
    assert result.audit.outcome is RawV2ReadOutcome.OBSERVED
    assert result.audit.reason is None
    result.audit.verify()


def test_binding_persists_exact_code_selector_and_authority(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    binding = _bind(fixture)
    cursor = fixture.connection.execute(
        """SELECT source_domain, source_version, stable_version, generation_id,
                  derived_entity_id, retrieval_unit_id, derivation_kind,
                  derivation_version, selector_kind, selector_json,
                  raw_content_sha256, selected_content_sha256,
                  parser_artifact_sha256, binding_json
           FROM raw_evidence_bindings_v2 WHERE locator_id=?""",
        (binding.locator_id,),
    )
    row = cursor.fetchone()
    assert row is not None
    stored = dict(zip((item[0] for item in cursor.description or ()), row, strict=True))

    identity = json.loads(stored.pop("binding_json"))
    assert stored == {
        "source_domain": "code",
        "source_version": REF,
        "stable_version": BLOB,
        "generation_id": GENERATION,
        "derived_entity_id": fixture.unit.entity_id,
        "retrieval_unit_id": fixture.unit.unit_id,
        "derivation_kind": "code_utf8_range",
        "derivation_version": CODE_RAW_GATEWAY_V2_VERSION,
        "selector_kind": "utf8_range_v2",
        "selector_json": (
            '{"selector":{"end_byte":71,"end_line":3,'
            '"newline_policy":"preserve_v1","start_byte":0,"start_line":1},'
            '"selector_kind":"utf8_range_v2"}'
        ),
        "raw_content_sha256": exact_bytes_sha256(fixture.payload),
        "selected_content_sha256": exact_bytes_sha256(fixture.unit.body.encode("utf-8")),
        "parser_artifact_sha256": None,
    }
    assert identity["adapter_version"] == "code-git-raw-v2"
    assert identity["schema_version"] == "code-raw-binding-v2"


def test_exact_binding_retry_is_idempotent(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    first = _bind(fixture)
    second = _bind(fixture)

    assert first.created is True
    assert second == replace(first, created=False)
    assert _binding_count(fixture.connection) == 1


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("wrong_raw_id", RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ("legacy_raw_id", RawV2Reason.CANONICALIZATION_MISMATCH),
        ("wrong_file_digest", RawV2Reason.RAW_DIGEST_MISMATCH),
        ("dirty_snapshot", RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ("wrong_head", RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ("wrong_entity", RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ("wrong_generation", RawV2Reason.SOURCE_IDENTITY_MISMATCH),
    ],
)
def test_invalid_code_lineage_fails_before_binding_mutation(
    tmp_path: Path,
    mutation: str,
    reason: RawV2Reason,
) -> None:
    fixture = _fixture(tmp_path)
    unit = fixture.unit
    if mutation == "wrong_raw_id":
        unit = _lineage_attributes(unit, raw_object_id="raw-v2:" + "0" * 64)
    elif mutation == "legacy_raw_id":
        unit = _lineage_attributes(unit, raw_object_id="raw://legacy/file-1")
    elif mutation == "wrong_file_digest":
        unit = replace(
            unit,
            source_lineage=replace(
                unit.source_lineage,
                file_content_hash="sha256:" + "0" * 64,
            ),
        )
    elif mutation == "dirty_snapshot":
        unit = _lineage_attributes(unit, snapshot_dirty="true")
    elif mutation == "wrong_head":
        unit = _lineage_attributes(unit, snapshot_head_commit="commit-other")
    elif mutation == "wrong_entity":
        unit = _lineage_attributes(unit, entity_id="entity-other")
    elif mutation == "wrong_generation":
        unit = _lineage_attributes(unit, generation_id="generation-other")
    else:  # pragma: no cover
        raise AssertionError(mutation)

    before = _binding_count(fixture.connection)
    with pytest.raises(RawV2ContractError) as error:
        _bind(fixture, unit=unit)
    _assert_reason(error, reason)
    assert _binding_count(fixture.connection) == before


@pytest.mark.parametrize("mutation", ["multibyte_split", "past_end", "wrong_line"])
def test_utf8_byte_and_line_disagreement_fails_before_binding_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = _fixture(tmp_path)
    unit = fixture.unit
    if mutation == "multibyte_split":
        inside_multibyte = fixture.payload.index("你".encode()) + 1
        unit = _unit_span(unit, start_byte=inside_multibyte)
    elif mutation == "past_end":
        unit = _unit_span(unit, end_byte=len(fixture.payload) + 1)
    elif mutation == "wrong_line":
        unit = _unit_span(unit, start_line=unit.span.start_line + 1)
    else:  # pragma: no cover
        raise AssertionError(mutation)

    with pytest.raises(RawV2ContractError) as error:
        _bind(fixture, unit=unit)
    _assert_reason(error, RawV2Reason.SELECTOR_OUT_OF_BOUNDS)
    assert _binding_count(fixture.connection) == 0


def test_unit_body_must_equal_exact_selected_source_bytes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    unit = replace(fixture.unit, body=fixture.unit.body + "# changed")

    with pytest.raises(RawV2ContractError) as error:
        _bind(fixture, unit=unit)
    _assert_reason(error, RawV2Reason.SELECTED_DIGEST_MISMATCH)
    assert _binding_count(fixture.connection) == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("project_id", "project-b"),
        ("raw_object_id", "raw-v2:" + "0" * 64),
        ("generation_id", "generation-other"),
        ("derived_entity_id", "entity-other"),
        ("retrieval_unit_id", "unit-other"),
        ("raw_content_sha256", "sha256:" + "0" * 64),
        ("selected_content_sha256", "sha256:" + "0" * 64),
    ],
)
def test_read_rejects_incoherent_unit_binding_before_service(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    fixture = _fixture(tmp_path)
    binding = _bind(fixture)
    changed = replace(binding, **{field: value})

    with pytest.raises(RawV2ContractError) as error:
        fixture.gateway.read_unit(request=_read_request(fixture, changed))
    _assert_reason(error, RawV2Reason.BINDING_DIGEST_MISMATCH)


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"project_id": "project-b"}, RawV2Reason.BINDING_NOT_FOUND),
        ({"repository_id": "repository-other"}, RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ({"file_path": "src/other.py"}, RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ({"ref": "commit-other"}, RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ({"blob_hash": "blob-other"}, RawV2Reason.STABLE_VERSION_MISMATCH),
        ({"generation_id": "generation-other"}, RawV2Reason.GENERATION_MISMATCH),
        ({"principal_id": "denied-reader"}, RawV2Reason.ACL_DENIED),
    ],
)
def test_wrong_target_scope_is_audited_by_shared_v28_service(
    tmp_path: Path,
    updates: dict[str, str],
    reason: RawV2Reason,
) -> None:
    fixture = _fixture(tmp_path)
    binding = _bind(fixture)
    result = fixture.gateway.read_unit(request=_read_request(fixture, binding, **updates))

    assert type(result) is RawEvidenceReadResultV2
    assert result.selected is None
    assert result.audit.outcome is RawV2ReadOutcome.REJECTED
    assert result.audit.reason is reason
    assert result.public.status_code == result.audit.public_status_code
    assert result.public.body.code is result.audit.public_disposition
    assert "snippet" not in result.public.body.model_dump()
    assert "page" not in result.public.body.model_dump()


def test_tombstoned_raw_evidence_has_no_code_or_v1_fallback(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    binding = _bind(fixture)
    RawV2TombstoneStore(
        connection=fixture.connection,
        policy=_policy(),
        clock=lambda: NOW,
    ).tombstone(
        request=RawTombstoneRequest(
            project_id=PROJECT,
            raw_object_id=fixture.raw_object_id,
            source_acl_ref=ACL,
            mutation_id="delete-code-file-1",
            reason_code="source_deleted",
            actor_digest="sha256:" + "a" * 64,
            event_time=OBSERVED,
            observed_at=OBSERVED,
            trace_id="trace-delete-code-file-1",
            schema_version="raw-tombstone-v1",
        )
    )

    result = fixture.gateway.read_unit(request=_read_request(fixture, binding))
    assert result.selected is None
    assert result.audit.outcome is RawV2ReadOutcome.REJECTED
    assert result.audit.reason in {
        RawV2Reason.DERIVATION_INVALIDATED,
        RawV2Reason.RAW_TOMBSTONED,
    }
    assert result.public.body.model_dump() == {"code": "evidence_unavailable"}


def test_gateway_has_no_repository_io_object_writer_or_fallback_capability() -> None:
    source = inspect.getsource(gateway_module)
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.lstrip(".").split(".", 1)[0])

    assert imported_roots.isdisjoint(
        {"asyncio", "http", "os", "pathlib", "requests", "socket", "subprocess", "urllib"}
    )
    for forbidden in (
        "RawV2ObjectStore",
        "RawV2EventStore",
        "open(",
        "read_text",
        "read_bytes",
        "git ",
        "snippet",
        "fallback",
    ):
        assert forbidden not in source


def test_file_digest_is_the_complete_raw_file_not_only_the_unit_body(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    assert fixture.unit.body.encode("utf-8") != fixture.payload
    assert fixture.unit.source_lineage.file_content_hash == exact_bytes_sha256(fixture.payload)
    assert exact_bytes_sha256(fixture.unit.body.encode("utf-8")) != exact_bytes_sha256(
        fixture.payload
    )
    binding = _bind(fixture)
    assert binding.raw_content_sha256 == exact_bytes_sha256(fixture.payload)
    assert binding.selected_content_sha256 == exact_bytes_sha256(fixture.unit.body.encode("utf-8"))


def test_private_acl_is_resolved_only_by_trusted_policy(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, source_acl_ref="source-private")
    binding = _bind(fixture)

    denied = fixture.gateway.read_unit(request=_read_request(fixture, binding))
    allowed = fixture.gateway.read_unit(
        request=_read_request(fixture, binding, principal_id="private-reader")
    )
    assert denied.selected is None
    assert denied.audit.reason is RawV2Reason.ACL_DENIED
    assert allowed.selected is not None
    assert allowed.selected.selected_bytes == fixture.unit.body.encode("utf-8")


def test_sha256_helper_matches_standard_library_for_fixture() -> None:
    payload = SOURCE.encode("utf-8")
    assert exact_bytes_sha256(payload) == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert CodeSourceLineage.__module__.endswith("unit_builder")
