from __future__ import annotations

import inspect
import os
import sqlite3
import stat
from dataclasses import FrozenInstanceError, dataclass, fields, replace
from pathlib import Path

import pytest

import evidence_rag.sources.v2.reader as reader_module
from evidence_rag.sources.v2.contracts import (
    RawV2ContractError,
    RawV2Reason,
    SourceDomain,
    exact_bytes_sha256,
)
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.reader import (
    RAW_V2_MANAGED_READER_VERSION,
    RawManagedReadRequest,
    RawManagedReadResult,
    RawV2ManagedReader,
)
from evidence_rag.sources.v2.schema import initialize_raw_v2_schema
from evidence_rag.sources.v2.store import (
    RawBindingRecord,
    RawBindingWriteRequest,
    RawObjectWriteRequest,
    RawTombstoneRequest,
    RawV2BindingStore,
    RawV2ObjectStore,
    RawV2TombstoneStore,
)

NOW = "2026-09-01T00:00:00.000000Z"
OBSERVED = "2026-08-31T23:59:00.000000Z"
ACTOR_DIGEST = "sha256:" + "a" * 64


class _Authority:
    projects = {"project-a", "project-b"}

    def project_exists(self, *, project_id: str) -> bool:
        return project_id in self.projects

    def resolve_object_acl_ref(
        self,
        *,
        project_id: str,
        source_acl_ref: str,
    ) -> str | None:
        if project_id not in self.projects:
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
        if project_id not in self.projects or principal_id == "unknown-principal":
            return None
        if principal_id == "private-reader":
            return (f"team-{project_id[-1]}",)
        return ()


def _policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


def _connection(path: Path | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:" if path is None else path)
    initialize_raw_v2_schema(connection)
    return connection


def _object_request(*, private: bool = False) -> RawObjectWriteRequest:
    return RawObjectWriteRequest(
        project_id="project-a",
        source_type="git",
        source_instance_id="repository-1",
        source_object_id="src/app.py",
        source_version="commit-1",
        stable_version="blob-1",
        source_acl_ref="source-private" if private else "source-public",
        media_type="text/plain",
        adapter_version="git-adapter-v2",
        schema_version="raw-object-v2",
        metadata_projection={},
        observed_at=OBSERVED,
    )


def _binding_request(*, raw_object_id: str, private: bool = False) -> RawBindingWriteRequest:
    return RawBindingWriteRequest(
        project_id="project-a",
        raw_object_id=raw_object_id,
        source_acl_ref="source-private" if private else "source-public",
        source_version="commit-1",
        stable_version="blob-1",
        generation_id="generation-1",
        derived_entity_id="entity-1",
        retrieval_unit_id="unit-1",
        derivation_kind="source_slice",
        derivation_version="derivation-v1",
        selector_kind="line_range",
        selector={"end_line": 2, "start_line": 1},
        parser_artifact_sha256=None,
        valid_from="2026-08-31T23:58:00Z",
        valid_to=None,
        observed_at=OBSERVED,
        adapter_version="git-adapter-v2",
        schema_version="raw-binding-v2",
    )


def _read_request(
    *,
    binding: RawBindingRecord,
    principal_id: str = "public-reader",
) -> RawManagedReadRequest:
    return RawManagedReadRequest(
        project_id="project-a",
        principal_id=principal_id,
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


@dataclass(slots=True)
class _Fixture:
    connection: sqlite3.Connection
    root: Path
    payload: bytes
    binding: RawBindingRecord
    request: RawManagedReadRequest
    reader: RawV2ManagedReader


def _fixture(
    tmp_path: Path,
    *,
    payload: bytes = b"line one\nline two\n",
    private: bool = False,
    database: Path | None = None,
    max_raw_bytes: int | None = None,
) -> _Fixture:
    connection = _connection(database)
    root = tmp_path / "blobs"
    root.mkdir(parents=True)
    object_store = RawV2ObjectStore(
        connection=connection,
        blob_root=root,
        policy=_policy(),
        clock=lambda: NOW,
    )
    raw = object_store.persist_bytes(
        request=_object_request(private=private),
        payload=payload,
    )
    binding_store = RawV2BindingStore(
        connection=connection,
        policy=_policy(),
        clock=lambda: NOW,
    )
    binding = binding_store.persist(
        request=_binding_request(raw_object_id=raw.raw_object_id, private=private),
        selected_content=b"line one\n",
    )
    request = _read_request(
        binding=binding,
        principal_id="private-reader" if private else "public-reader",
    )
    return _Fixture(
        connection=connection,
        root=root,
        payload=payload,
        binding=binding,
        request=request,
        reader=RawV2ManagedReader(
            connection=connection,
            blob_root=root,
            policy=_policy(),
            max_raw_bytes=max_raw_bytes or max(1, len(payload) + 1),
        ),
    )


def _assert_reason(
    expected: RawV2Reason,
    call,
    /,
    *args: object,
    **kwargs: object,
) -> RawV2ContractError:
    with pytest.raises(RawV2ContractError) as captured:
        call(*args, **kwargs)
    assert captured.value.reason is expected
    return captured.value


def _row(connection: sqlite3.Connection, table: str) -> dict[str, object]:
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    value = cursor.fetchone()
    assert value is not None
    return dict(zip((str(item[0]) for item in cursor.description or ()), value, strict=True))


def _storage_target(fixture: _Fixture) -> Path:
    return fixture.root / str(_row(fixture.connection, "raw_blobs_v2")["storage_key"])


def _database_dump(connection: sqlite3.Connection) -> str:
    return "\n".join(connection.iterdump())


def _tree_projection(root: Path) -> tuple[tuple[object, ...], ...]:
    projected: list[tuple[object, ...]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        for name in sorted([*names, *files]):
            item = parent / name
            relative = item.relative_to(root).as_posix()
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                projected.append(("symlink", relative, os.readlink(item)))
            elif stat.S_ISDIR(metadata.st_mode):
                projected.append(("directory", relative))
            elif stat.S_ISREG(metadata.st_mode):
                raw = item.read_bytes()
                projected.append(("file", relative, len(raw), exact_bytes_sha256(raw)))
            else:
                projected.append(("other", relative, stat.S_IFMT(metadata.st_mode)))
    return tuple(sorted(projected))


def test_reader_api_is_logical_frozen_and_has_no_override_or_runtime_surface() -> None:
    assert RAW_V2_MANAGED_READER_VERSION == "raw-v2-managed-reader-v1"
    assert [field.name for field in fields(RawManagedReadRequest)] == [
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
    ]
    assert [field.name for field in fields(RawManagedReadResult)] == [
        "project_id",
        "locator_id",
        "binding_sha256",
        "raw_object_id",
        "source_domain",
        "source_type",
        "source_instance_id",
        "source_object_id",
        "source_version",
        "stable_version",
        "generation_id",
        "media_type",
        "raw_content_sha256",
        "byte_length",
        "selector_kind",
        "selector_json",
        "selector_sha256",
        "selected_content_sha256",
        "parser_artifact_sha256",
        "raw_bytes",
    ]
    prohibited = {
        "path",
        "storage_path",
        "storage_key",
        "raw_digest",
        "acl_ref",
        "acl_refs",
        "visibility_partition_sha256",
        "selector",
        "mode",
        "fallback",
    }
    assert prohibited.isdisjoint(field.name for field in fields(RawManagedReadRequest))
    assert prohibited.isdisjoint(field.name for field in fields(RawManagedReadResult))
    assert list(inspect.signature(RawV2ManagedReader).parameters) == [
        "connection",
        "blob_root",
        "policy",
        "max_raw_bytes",
    ]
    assert list(inspect.signature(RawV2ManagedReader.read).parameters) == ["self", "request"]


def test_request_and_result_are_exact_frozen_values(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with pytest.raises(FrozenInstanceError):
        fixture.request.project_id = "project-b"  # type: ignore[misc]
    result = fixture.reader.read(request=fixture.request)
    with pytest.raises(FrozenInstanceError):
        result.raw_bytes = b"changed"  # type: ignore[misc]
    _assert_reason(
        RawV2Reason.CANONICALIZATION_MISMATCH,
        fixture.reader.read,
        request=object(),
    )


@pytest.mark.parametrize("private", [False, True])
def test_public_and_private_acl_positive_read_returns_exact_preselector_result(
    tmp_path: Path,
    private: bool,
) -> None:
    fixture = _fixture(tmp_path, private=private)
    before_db = _database_dump(fixture.connection)
    before_tree = _tree_projection(fixture.root)
    result = fixture.reader.read(request=fixture.request)
    assert result == RawManagedReadResult(
        project_id="project-a",
        locator_id=fixture.binding.locator_id,
        binding_sha256=fixture.binding.binding_sha256,
        raw_object_id=fixture.binding.raw_object_id,
        source_domain=SourceDomain.CODE,
        source_type="git",
        source_instance_id="repository-1",
        source_object_id="src/app.py",
        source_version="commit-1",
        stable_version="blob-1",
        generation_id="generation-1",
        media_type="text/plain",
        raw_content_sha256=exact_bytes_sha256(fixture.payload),
        byte_length=len(fixture.payload),
        selector_kind="line_range",
        selector_json='{"selector":{"end_line":2,"start_line":1},"selector_kind":"line_range"}',
        selector_sha256=fixture.binding.selector_sha256,
        selected_content_sha256=fixture.binding.selected_content_sha256,
        parser_artifact_sha256=None,
        raw_bytes=fixture.payload,
    )
    assert _database_dump(fixture.connection) == before_db
    assert _tree_projection(fixture.root) == before_tree


@pytest.mark.parametrize("payload", [b"", b"x" * (2 * 1024 * 1024 + 17)])
def test_empty_and_multichunk_payloads_read_exactly(tmp_path: Path, payload: bytes) -> None:
    fixture = _fixture(tmp_path, payload=payload, max_raw_bytes=max(1, len(payload) + 1))
    result = fixture.reader.read(request=fixture.request)
    assert result.raw_bytes == payload
    assert result.byte_length == len(payload)
    assert result.raw_content_sha256 == exact_bytes_sha256(payload)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"expected_binding_sha256": "sha256:" + "0" * 64}, RawV2Reason.BINDING_DIGEST_MISMATCH),
        ({"expected_source_domain": SourceDomain.DOCUMENT}, RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ({"expected_source_type": "document"}, RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ({"expected_source_instance_id": "repository-2"}, RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ({"expected_source_object_id": "src/other.py"}, RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ({"expected_source_version": "commit-2"}, RawV2Reason.SOURCE_IDENTITY_MISMATCH),
        ({"expected_stable_version": "blob-2"}, RawV2Reason.STABLE_VERSION_MISMATCH),
        ({"expected_generation_id": "generation-2"}, RawV2Reason.GENERATION_MISMATCH),
    ],
)
def test_expected_authority_mismatch_fails_before_blob_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, object],
    reason: RawV2Reason,
) -> None:
    fixture = _fixture(tmp_path)

    def forbidden_open(*args: object, **kwargs: object) -> int:
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(reader_module.os, "open", forbidden_open)
    _assert_reason(reason, fixture.reader.read, request=replace(fixture.request, **change))


def test_project_locator_and_acl_fail_closed_without_reading_bytes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, private=True)
    _assert_reason(
        RawV2Reason.PROJECT_MISMATCH,
        fixture.reader.read,
        request=replace(fixture.request, project_id="unknown-project"),
    )
    _assert_reason(
        RawV2Reason.BINDING_NOT_FOUND,
        fixture.reader.read,
        request=replace(fixture.request, project_id="project-b"),
    )
    _assert_reason(
        RawV2Reason.BINDING_NOT_FOUND,
        fixture.reader.read,
        request=replace(fixture.request, locator_id="raw-locator-v2:" + "0" * 64),
    )
    _assert_reason(
        RawV2Reason.ACL_DENIED,
        fixture.reader.read,
        request=replace(fixture.request, principal_id="public-reader"),
    )
    _assert_reason(
        RawV2Reason.ACL_DENIED,
        fixture.reader.read,
        request=replace(fixture.request, principal_id="unknown-principal"),
    )


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ("quarantined", RawV2Reason.RAW_QUARANTINED),
        ("tombstoned", RawV2Reason.RAW_TOMBSTONED),
        ("corrupt", RawV2Reason.RAW_STATE_INVALID),
    ],
)
def test_nonactive_raw_states_fail_closed(
    tmp_path: Path,
    state: str,
    reason: RawV2Reason,
) -> None:
    fixture = _fixture(tmp_path)
    tombstoned_at = NOW if state == "tombstoned" else None
    fixture.connection.execute(
        "UPDATE raw_objects_v2 SET state=?, tombstoned_at=?",
        (state, tombstoned_at),
    )
    fixture.connection.commit()
    _assert_reason(reason, fixture.reader.read, request=fixture.request)


def test_missing_and_reference_only_raw_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.connection.execute("PRAGMA foreign_keys=OFF")
    original_binding_row = RawV2ManagedReader._binding_row

    def delete_after_binding_lookup(
        self: RawV2ManagedReader,
        request: object,
    ) -> dict[str, object]:
        row = original_binding_row(self, request)  # type: ignore[arg-type]
        fixture.connection.execute("DELETE FROM raw_objects_v2")
        fixture.connection.commit()
        return row

    monkeypatch.setattr(RawV2ManagedReader, "_binding_row", delete_after_binding_lookup)
    _assert_reason(RawV2Reason.RAW_NOT_FOUND, fixture.reader.read, request=fixture.request)

    monkeypatch.setattr(RawV2ManagedReader, "_binding_row", original_binding_row)

    second = _fixture(tmp_path / "second")
    second.connection.execute(
        """UPDATE raw_objects_v2
           SET state='reference_only', raw_content_sha256=NULL,
               blob_sha256=NULL, byte_length=NULL"""
    )
    second.connection.commit()
    _assert_reason(
        RawV2Reason.REFERENCE_ONLY_UNAVAILABLE,
        second.reader.read,
        request=second.request,
    )


def test_invalidated_and_blocked_derivation_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.connection.execute(
        "UPDATE raw_evidence_bindings_v2 SET invalidated_at=?, invalidation_reason=?",
        (NOW, "source_deleted"),
    )
    fixture.connection.commit()
    _assert_reason(
        RawV2Reason.DERIVATION_INVALIDATED,
        fixture.reader.read,
        request=fixture.request,
    )

    second = _fixture(tmp_path / "second")
    second.connection.execute(
        """INSERT INTO blocked_entities_v2(
               project_id, entity_id, raw_object_id, reason_code, blocked_at, actor_digest
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            "project-a",
            "entity-1",
            second.binding.raw_object_id,
            "source_deleted",
            NOW,
            ACTOR_DIGEST,
        ),
    )
    second.connection.commit()
    _assert_reason(
        RawV2Reason.DERIVATION_INVALIDATED,
        second.reader.read,
        request=second.request,
    )


def test_binding_and_raw_logical_identity_tamper_fail_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.connection.execute(
        "UPDATE raw_evidence_bindings_v2 SET binding_json=replace(binding_json, 'unit-1', 'unit-x')"
    )
    fixture.connection.commit()
    _assert_reason(
        RawV2Reason.BINDING_DIGEST_MISMATCH,
        fixture.reader.read,
        request=fixture.request,
    )

    second = _fixture(tmp_path / "second")
    second.connection.execute(
        "UPDATE raw_objects_v2 SET logical_identity_sha256=?",
        ("sha256:" + "0" * 64,),
    )
    second.connection.commit()
    _assert_reason(
        RawV2Reason.SOURCE_IDENTITY_MISMATCH,
        second.reader.read,
        request=second.request,
    )


@pytest.mark.parametrize(
    "storage_key",
    [
        "/tmp/outside",
        "../../outside",
        "aa/../outside",
        "file:///tmp/outside",
        "https://example.invalid/blob",
        "aa\\bb\\blob",
        "00/00/" + "0" * 64,
    ],
)
def test_noncanonical_or_outside_storage_key_is_rejected(
    tmp_path: Path,
    storage_key: str,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.connection.execute("UPDATE raw_blobs_v2 SET storage_key=?", (storage_key,))
    fixture.connection.commit()
    _assert_reason(
        RawV2Reason.STORAGE_OUTSIDE_ROOT,
        fixture.reader.read,
        request=fixture.request,
    )


def test_relative_root_root_symlink_and_symlink_ancestor_are_rejected(tmp_path: Path) -> None:
    connection = _connection()
    _assert_reason(
        RawV2Reason.STORAGE_OUTSIDE_ROOT,
        RawV2ManagedReader,
        connection=connection,
        blob_root=Path("relative/blobs"),
        policy=_policy(),
        max_raw_bytes=1024,
    )

    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    _assert_reason(
        RawV2Reason.STORAGE_SYMLINK,
        RawV2ManagedReader,
        connection=connection,
        blob_root=alias,
        policy=_policy(),
        max_raw_bytes=1024,
    )

    nested = real / "nested/blobs"
    nested.mkdir(parents=True)
    ancestor_alias = tmp_path / "ancestor-alias"
    ancestor_alias.symlink_to(real, target_is_directory=True)
    _assert_reason(
        RawV2Reason.STORAGE_SYMLINK,
        RawV2ManagedReader,
        connection=connection,
        blob_root=ancestor_alias / "nested/blobs",
        policy=_policy(),
        max_raw_bytes=1024,
    )


def test_root_identity_replacement_is_rejected(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    original = tmp_path / "original-blobs"
    fixture.root.rename(original)
    fixture.root.mkdir()
    _assert_reason(
        RawV2Reason.STORAGE_OUTSIDE_ROOT,
        fixture.reader.read,
        request=fixture.request,
    )


@pytest.mark.parametrize("component_index", [0, 1, 2])
def test_symlink_at_every_storage_component_is_rejected(
    tmp_path: Path,
    component_index: int,
) -> None:
    fixture = _fixture(tmp_path)
    target = _storage_target(fixture)
    parts = target.relative_to(fixture.root).parts
    victim = fixture.root.joinpath(*parts[: component_index + 1])
    outside = tmp_path / f"outside-{component_index}"
    if victim.is_dir():
        victim.rename(outside)
        victim.symlink_to(outside, target_is_directory=True)
    else:
        outside.write_bytes(victim.read_bytes())
        victim.unlink()
        victim.symlink_to(outside)
    _assert_reason(
        RawV2Reason.STORAGE_SYMLINK,
        fixture.reader.read,
        request=fixture.request,
    )


@pytest.mark.parametrize("target_kind", ["directory", "fifo"])
def test_final_target_must_be_a_regular_file(tmp_path: Path, target_kind: str) -> None:
    fixture = _fixture(tmp_path)
    target = _storage_target(fixture)
    target.unlink()
    if target_kind == "directory":
        target.mkdir()
    else:
        os.mkfifo(target)
    _assert_reason(
        RawV2Reason.STORAGE_NOT_REGULAR,
        fixture.reader.read,
        request=fixture.request,
    )


def test_missing_directory_and_file_are_storage_unavailable(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    target = _storage_target(fixture)
    target.unlink()
    _assert_reason(
        RawV2Reason.STORAGE_UNAVAILABLE,
        fixture.reader.read,
        request=fixture.request,
    )

    second = _fixture(tmp_path / "second")
    target = _storage_target(second)
    prefix = second.root / target.relative_to(second.root).parts[0]
    for item in sorted(prefix.rglob("*"), reverse=True):
        item.rmdir() if item.is_dir() else item.unlink()
    prefix.rmdir()
    _assert_reason(
        RawV2Reason.STORAGE_UNAVAILABLE,
        second.reader.read,
        request=second.request,
    )


def test_size_ceiling_short_appended_and_same_length_digest_tamper_are_distinct(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, payload=b"12345678", max_raw_bytes=7)
    _assert_reason(
        RawV2Reason.CONTRACT_SIZE_EXCEEDED,
        fixture.reader.read,
        request=fixture.request,
    )

    short = _fixture(tmp_path / "short", payload=b"12345678")
    _storage_target(short).write_bytes(b"123")
    _assert_reason(
        RawV2Reason.BYTE_LENGTH_MISMATCH,
        short.reader.read,
        request=short.request,
    )

    appended = _fixture(tmp_path / "appended", payload=b"12345678")
    _storage_target(appended).write_bytes(b"123456789")
    _assert_reason(
        RawV2Reason.BYTE_LENGTH_MISMATCH,
        appended.reader.read,
        request=appended.request,
    )

    changed = _fixture(tmp_path / "changed", payload=b"12345678")
    _storage_target(changed).write_bytes(b"abcdefgh")
    _assert_reason(
        RawV2Reason.RAW_DIGEST_MISMATCH,
        changed.reader.read,
        request=changed.request,
    )


@pytest.mark.parametrize("storage_state", ["missing", "quarantined", "corrupt"])
def test_blob_metadata_state_never_reads_file(
    tmp_path: Path,
    storage_state: str,
) -> None:
    fixture = _fixture(tmp_path)
    fixture.connection.execute(
        "UPDATE raw_blobs_v2 SET storage_state=?",
        (storage_state,),
    )
    fixture.connection.commit()
    expected = (
        RawV2Reason.RAW_QUARANTINED
        if storage_state == "quarantined"
        else RawV2Reason.STORAGE_UNAVAILABLE
    )
    _assert_reason(expected, fixture.reader.read, request=fixture.request)


@pytest.mark.parametrize(
    ("replacement", "reason"),
    [
        ("symlink", RawV2Reason.STORAGE_SYMLINK),
        ("directory", RawV2Reason.STORAGE_OUTSIDE_ROOT),
    ],
)
def test_component_swap_between_lstat_and_open_cannot_escape_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
    reason: RawV2Reason,
) -> None:
    fixture = _fixture(tmp_path)
    first = _storage_target(fixture).relative_to(fixture.root).parts[0]
    original_stat = reader_module.os.stat
    swapped = False

    def swapping_stat(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ):
        nonlocal swapped
        result = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == first and dir_fd is not None and not follow_symlinks and not swapped:
            swapped = True
            victim = fixture.root / first
            outside = tmp_path / "swapped-outside"
            victim.rename(outside)
            if replacement == "symlink":
                victim.symlink_to(outside, target_is_directory=True)
            else:
                victim.mkdir()
        return result

    monkeypatch.setattr(reader_module.os, "stat", swapping_stat)
    _assert_reason(
        reason,
        fixture.reader.read,
        request=fixture.request,
    )
    assert swapped is True


def test_regular_file_swap_between_lstat_and_open_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path, payload=b"authority")
    target = _storage_target(fixture)
    filename = target.name
    original_stat = reader_module.os.stat
    swapped = False

    def swapping_stat(
        path: object,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ):
        nonlocal swapped
        result = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == filename and dir_fd is not None and not follow_symlinks and not swapped:
            swapped = True
            target.rename(tmp_path / "original-file")
            target.write_bytes(b"malicious")
        return result

    monkeypatch.setattr(reader_module.os, "stat", swapping_stat)
    _assert_reason(
        RawV2Reason.STORAGE_OUTSIDE_ROOT,
        fixture.reader.read,
        request=fixture.request,
    )
    assert swapped is True


def test_tombstone_after_physical_read_is_stopped_by_fresh_output_recheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "reader-race.sqlite3"
    fixture = _fixture(tmp_path, database=database)
    writer = sqlite3.connect(database)
    original = RawV2BindingStore.recheck_before_output
    tombstoned = False

    def tombstone_then_recheck(
        self: RawV2BindingStore,
        *,
        record: RawBindingRecord,
    ) -> RawBindingRecord:
        nonlocal tombstoned
        if not tombstoned:
            RawV2TombstoneStore(
                connection=writer,
                policy=_policy(),
                clock=lambda: NOW,
            ).tombstone(
                request=RawTombstoneRequest(
                    project_id="project-a",
                    raw_object_id=record.raw_object_id,
                    source_acl_ref="source-public",
                    mutation_id="delete-1",
                    reason_code="source_deleted",
                    actor_digest=ACTOR_DIGEST,
                    event_time=None,
                    observed_at=NOW,
                    trace_id="trace-delete-1",
                    schema_version="raw-tombstone-v1",
                )
            )
            tombstoned = True
        return original(self, record=record)

    monkeypatch.setattr(RawV2BindingStore, "recheck_before_output", tombstone_then_recheck)
    _assert_reason(
        RawV2Reason.BINDING_DIGEST_MISMATCH,
        fixture.reader.read,
        request=fixture.request,
    )
    assert tombstoned is True
    writer.close()


def test_failure_paths_close_descriptors_and_never_mutate_db(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    target = _storage_target(fixture)
    target.unlink()
    before_db = _database_dump(fixture.connection)
    descriptor_root = Path("/dev/fd")
    before_fds = len(tuple(descriptor_root.iterdir())) if descriptor_root.is_dir() else None
    for _ in range(50):
        _assert_reason(
            RawV2Reason.STORAGE_UNAVAILABLE,
            fixture.reader.read,
            request=fixture.request,
        )
    after_fds = len(tuple(descriptor_root.iterdir())) if descriptor_root.is_dir() else None
    if before_fds is not None and after_fds is not None:
        assert after_fds <= before_fds + 1
    assert _database_dump(fixture.connection) == before_db


def test_caller_transaction_and_schema_drift_fail_before_storage_read(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.connection.execute("BEGIN")
    _assert_reason(
        RawV2Reason.RAW_STATE_INVALID,
        fixture.reader.read,
        request=fixture.request,
    )
    fixture.connection.rollback()

    fixture.connection.execute("DROP INDEX idx_bindings_v2_raw")
    fixture.connection.commit()
    _assert_reason(
        RawV2Reason.RAW_STATE_INVALID,
        fixture.reader.read,
        request=fixture.request,
    )


def test_unreadable_regular_target_is_storage_unavailable(tmp_path: Path) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root can read mode-zero fixtures")
    fixture = _fixture(tmp_path)
    target = _storage_target(fixture)
    target.chmod(0)
    try:
        _assert_reason(
            RawV2Reason.STORAGE_UNAVAILABLE,
            fixture.reader.read,
            request=fixture.request,
        )
    finally:
        target.chmod(0o600)


@pytest.mark.parametrize("max_raw_bytes", [True, 0, -1, 1.5, "1024", None])
def test_constructor_rejects_nonpositive_or_nonexact_size_ceiling(
    tmp_path: Path,
    max_raw_bytes: object,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    _assert_reason(
        RawV2Reason.CANONICALIZATION_MISMATCH,
        RawV2ManagedReader,
        connection=connection,
        blob_root=root,
        policy=_policy(),
        max_raw_bytes=max_raw_bytes,
    )
