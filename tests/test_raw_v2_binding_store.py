from __future__ import annotations

import inspect
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from pathlib import Path

import pytest

from evidence_rag.sources.v2.contracts import (
    RawV2ContractError,
    RawV2Reason,
    canonical_json_bytes,
    exact_bytes_sha256,
)
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.schema import RAW_V2_TABLES, initialize_raw_v2_schema
from evidence_rag.sources.v2.store import (
    RAW_V2_BINDING_STORE_VERSION,
    RawBindingRecord,
    RawBindingResolveRequest,
    RawBindingWriteRequest,
    RawEventWriteRequest,
    RawObjectWriteRequest,
    RawV2BindingStore,
    RawV2EventStore,
    RawV2ObjectStore,
)

NOW = "2026-09-01T00:00:00.000000Z"
LATER = "2026-09-01T00:00:10.000000Z"
OBSERVED = "2026-08-31T23:59:00.000000Z"


class _Authority:
    projects = {"project-a", "project-b", "project-é"}

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
            return "acl-private" if project_id == "project-é" else f"team-{project_id[-1]}"
        return None

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        del principal_id
        if project_id not in self.projects:
            return None
        return ()


def _policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


def _connection(path: Path | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:" if path is None else path)
    initialize_raw_v2_schema(connection)
    return connection


def _object_request(
    *,
    project_id: str = "project-a",
    source_acl_ref: str = "source-public",
    source_instance_id: str = "repository-1",
    source_object_id: str = "object-1",
    source_version: str = "version-1",
    stable_version: str = "stable-1",
    observed_at: str = OBSERVED,
) -> RawObjectWriteRequest:
    return RawObjectWriteRequest(
        project_id=project_id,
        source_type="git",
        source_instance_id=source_instance_id,
        source_object_id=source_object_id,
        source_version=source_version,
        stable_version=stable_version,
        source_acl_ref=source_acl_ref,
        media_type="text/plain",
        adapter_version="git-adapter-v2",
        schema_version="raw-object-v2",
        metadata_projection={},
        observed_at=observed_at,
    )


def _object_store(connection: sqlite3.Connection, root: Path) -> RawV2ObjectStore:
    return RawV2ObjectStore(
        connection=connection,
        blob_root=root,
        policy=_policy(),
        clock=lambda: NOW,
    )


def _binding_request(
    *,
    project_id: str = "project-a",
    raw_object_id: str,
    source_acl_ref: str = "source-public",
    source_version: str = "version-1",
    stable_version: str = "stable-1",
    generation_id: str = "generation-1",
    derived_entity_id: str = "entity-1",
    retrieval_unit_id: str = "unit-1",
    derivation_kind: str = "source_slice",
    derivation_version: str = "derivation-v1",
    selector_kind: str = "line_range",
    selector: dict[str, object] | None = None,
    parser_artifact_sha256: str | None = None,
    valid_from: str | None = "2026-08-31T23:58:00Z",
    valid_to: str | None = None,
    observed_at: str = OBSERVED,
    adapter_version: str = "git-adapter-v2",
    schema_version: str = "raw-binding-v2",
) -> RawBindingWriteRequest:
    return RawBindingWriteRequest(
        project_id=project_id,
        raw_object_id=raw_object_id,
        source_acl_ref=source_acl_ref,
        source_version=source_version,
        stable_version=stable_version,
        generation_id=generation_id,
        derived_entity_id=derived_entity_id,
        retrieval_unit_id=retrieval_unit_id,
        derivation_kind=derivation_kind,
        derivation_version=derivation_version,
        selector_kind=selector_kind,
        selector=selector or {"end_line": 8, "include_context": True, "start_line": 3},
        parser_artifact_sha256=parser_artifact_sha256,
        valid_from=valid_from,
        valid_to=valid_to,
        observed_at=observed_at,
        adapter_version=adapter_version,
        schema_version=schema_version,
    )


def _binding_store(
    connection: sqlite3.Connection,
    *,
    clock: Callable[[], str] | None = None,
) -> RawV2BindingStore:
    return RawV2BindingStore(
        connection=connection,
        policy=_policy(),
        clock=clock or (lambda: NOW),
    )


def _active_object(
    connection: sqlite3.Connection,
    root: Path,
    *,
    payload: bytes = b"raw binding payload",
    request: RawObjectWriteRequest | None = None,
):
    return _object_store(connection, root).persist_bytes(
        request=request or _object_request(),
        payload=payload,
    )


def _tree_projection(root: Path) -> list[dict[str, object]]:
    projected: list[dict[str, object]] = []
    if not root.exists() and not root.is_symlink():
        return projected
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        names.sort()
        files.sort()
        for name in [*names, *files]:
            item = parent / name
            relative = item.relative_to(root).as_posix()
            mode = item.lstat().st_mode
            if stat.S_ISLNK(mode):
                projected.append(
                    {"kind": "symlink", "path": relative, "target": os.readlink(item)}
                )
            elif stat.S_ISDIR(mode):
                projected.append({"kind": "directory", "path": relative})
            elif stat.S_ISREG(mode):
                raw = item.read_bytes()
                projected.append(
                    {
                        "kind": "file",
                        "path": relative,
                        "byte_length": len(raw),
                        "sha256": exact_bytes_sha256(raw),
                    }
                )
            else:
                projected.append({"kind": "other", "path": relative})
    return sorted(projected, key=lambda item: (str(item["path"]), str(item["kind"])))


def _database_projection(connection: sqlite3.Connection) -> dict[str, object]:
    projected: dict[str, object] = {}
    for table in RAW_V2_TABLES:
        columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
        rows = [
            list(row)
            for row in connection.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid'
            ).fetchall()
        ]
        projected[table] = {"columns": columns, "rows": rows}
    return projected


def _state_sha256(connection: sqlite3.Connection, root: Path) -> str:
    return exact_bytes_sha256(
        canonical_json_bytes(
            {
                "database": _database_projection(connection),
                "blob_tree": _tree_projection(root),
            },
            allow_none=True,
        )
    )


def _count(connection: sqlite3.Connection, table: str) -> int:
    value = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    assert value is not None
    return int(value[0])


def _binding_row(connection: sqlite3.Connection) -> dict[str, object]:
    cursor = connection.execute("SELECT * FROM raw_evidence_bindings_v2")
    row = cursor.fetchone()
    assert row is not None
    return dict(zip((str(item[0]) for item in cursor.description or ()), row, strict=True))


def _assert_reason(
    error: pytest.ExceptionInfo[RawV2ContractError],
    reason: RawV2Reason,
) -> None:
    assert error.value.reason is reason


def test_binding_api_accepts_only_trusted_inputs_and_never_returns_selected_bytes() -> None:
    assert RAW_V2_BINDING_STORE_VERSION == "raw-v2-binding-store-v1"
    assert {item.name for item in fields(RawBindingWriteRequest)} == {
        "project_id",
        "raw_object_id",
        "source_acl_ref",
        "source_version",
        "stable_version",
        "generation_id",
        "derived_entity_id",
        "retrieval_unit_id",
        "derivation_kind",
        "derivation_version",
        "selector_kind",
        "selector",
        "parser_artifact_sha256",
        "valid_from",
        "valid_to",
        "observed_at",
        "adapter_version",
        "schema_version",
    }
    assert {item.name for item in fields(RawBindingResolveRequest)} == {
        "project_id",
        "locator_id",
        "derived_entity_id",
        "retrieval_unit_id",
        "generation_id",
    }
    assert {item.name for item in fields(RawBindingRecord)} == {
        "locator_id",
        "binding_sha256",
        "project_id",
        "raw_object_id",
        "generation_id",
        "derived_entity_id",
        "retrieval_unit_id",
        "selector_sha256",
        "raw_content_sha256",
        "selected_content_sha256",
        "created",
    }
    constructor = inspect.signature(RawV2BindingStore).parameters
    assert set(constructor) == {"connection", "policy", "clock"}
    assert "blob_root" not in constructor
    assert "selected_content" in inspect.signature(RawV2BindingStore.persist).parameters


def test_portable_binding_locator_and_canonical_projection_golden(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(
        connection,
        root,
        payload=b"print('authority')\n",
        request=_object_request(
            project_id="project-é",
            source_acl_ref="source-private",
            source_instance_id="repo-main",
            source_object_id="src/app.py",
            source_version="commit-abc123",
            stable_version="blob-def456",
            observed_at="2026-08-31T14:00:01.250000Z",
        ),
    )
    request = _binding_request(
        project_id="project-é",
        raw_object_id=raw.raw_object_id,
        source_acl_ref="source-private",
        source_version="commit-abc123",
        stable_version="blob-def456",
        generation_id="generation-001",
        derived_entity_id="entity-app",
        retrieval_unit_id="unit-lines-3-8",
        valid_from="2026-08-31T14:00:00Z",
        observed_at="2026-08-31T14:00:01.250000+00:00",
    )

    result = _binding_store(connection).persist(
        request=request,
        selected_content=b"authority",
    )
    row = _binding_row(connection)

    assert result.locator_id == (
        "raw-locator-v2:3bb2f378010aa160fee04074e712a19243cf303dd13bd155e74be91761a051d0"
    )
    assert result.binding_sha256 == (
        "sha256:3bb2f378010aa160fee04074e712a19243cf303dd13bd155e74be91761a051d0"
    )
    assert result.selector_sha256 == (
        "sha256:888e6a88bc73a2de97c2ab04df384cd63dbe73bbcecc39c9ff8362feab0f76d7"
    )
    assert result.raw_content_sha256 == exact_bytes_sha256(b"print('authority')\n")
    assert result.selected_content_sha256 == exact_bytes_sha256(b"authority")
    assert row["selector_json"] == json.dumps(
        {
            "selector": {"end_line": 8, "include_context": True, "start_line": 3},
            "selector_kind": "line_range",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert json.loads(str(row["binding_json"]))["selected_content_sha256"] == (
        result.selected_content_sha256
    )
    assert b"authority" not in str(row).encode("utf-8")


def test_exact_retry_is_write_free_and_ignores_only_created_at(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    request = _binding_request(raw_object_id=raw.raw_object_id)
    times = iter((NOW, LATER))
    store = _binding_store(connection, clock=lambda: next(times))

    first = store.persist(request=request, selected_content=b"selected")
    before = _state_sha256(connection, root)
    second = store.persist(request=request, selected_content=b"selected")

    assert first.created is True
    assert second == replace(first, created=False)
    assert _state_sha256(connection, root) == before
    assert _binding_row(connection)["created_at"] == NOW


def test_same_authority_across_projects_and_acls_has_distinct_binding_identity(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    results: list[RawBindingRecord] = []
    for project_id, acl in (
        ("project-a", "source-public"),
        ("project-a", "source-private"),
        ("project-b", "source-public"),
    ):
        raw = _active_object(
            connection,
            root,
            request=_object_request(project_id=project_id, source_acl_ref=acl),
        )
        results.append(
            _binding_store(connection).persist(
                request=_binding_request(
                    project_id=project_id,
                    raw_object_id=raw.raw_object_id,
                    source_acl_ref=acl,
                ),
                selected_content=b"selected",
            )
        )

    assert len({item.locator_id for item in results}) == 3
    assert _count(connection, "raw_evidence_bindings_v2") == 3
    assert (
        _binding_store(connection).resolve(
            request=RawBindingResolveRequest(
                project_id="project-b",
                locator_id=results[0].locator_id,
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("state", "expected_reason"),
    [
        ("reference_only", RawV2Reason.REFERENCE_ONLY_UNAVAILABLE),
        ("quarantined", RawV2Reason.RAW_STATE_INVALID),
        ("tombstoned", RawV2Reason.RAW_STATE_INVALID),
        ("corrupt", RawV2Reason.RAW_STATE_INVALID),
    ],
)
def test_only_active_byte_backed_objects_can_bind(
    tmp_path: Path,
    state: str,
    expected_reason: RawV2Reason,
) -> None:
    connection = _connection()
    root = tmp_path / state
    root.mkdir()
    if state == "reference_only":
        raw = _object_store(connection, root).persist_reference(request=_object_request())
    else:
        raw = _active_object(connection, root)
        connection.execute(
            "UPDATE raw_objects_v2 SET state=?, tombstoned_at=? WHERE raw_object_id=?",
            (state, NOW if state == "tombstoned" else None, raw.raw_object_id),
        )
        connection.commit()
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as captured:
        _binding_store(connection).persist(
            request=_binding_request(raw_object_id=raw.raw_object_id),
            selected_content=b"selected",
        )

    _assert_reason(captured, expected_reason)
    assert _state_sha256(connection, root) == before
    assert _count(connection, "raw_evidence_bindings_v2") == 0


@pytest.mark.parametrize(
    "change",
    [
        {"project_id": "project-b"},
        {"source_acl_ref": "source-private"},
        {"source_version": "wrong-version"},
        {"stable_version": "wrong-stable"},
    ],
)
def test_binding_requires_exact_project_acl_and_versions(
    tmp_path: Path,
    change: dict[str, str],
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    before = _state_sha256(connection, root)
    request = replace(_binding_request(raw_object_id=raw.raw_object_id), **change)

    with pytest.raises(RawV2ContractError) as captured:
        _binding_store(connection).persist(
            request=request,
            selected_content=b"selected",
        )

    assert captured.value.reason in {
        RawV2Reason.RAW_STATE_INVALID,
        RawV2Reason.VISIBILITY_MISMATCH,
    }
    assert _state_sha256(connection, root) == before


@pytest.mark.parametrize(
    ("table", "assignment", "value"),
    [
        ("raw_objects_v2", "tombstoned_at", NOW),
        ("raw_blobs_v2", "storage_state", "corrupt"),
    ],
)
def test_active_object_marker_and_blob_metadata_tamper_cannot_bind(
    tmp_path: Path,
    table: str,
    assignment: str,
    value: str,
) -> None:
    connection = _connection()
    root = tmp_path / assignment
    root.mkdir()
    raw = _active_object(connection, root)
    identity_column = "raw_object_id" if table == "raw_objects_v2" else "blob_sha256"
    identity_value = raw.raw_object_id if table == "raw_objects_v2" else raw.raw_content_sha256
    connection.execute(
        f'UPDATE "{table}" SET "{assignment}"=? WHERE "{identity_column}"=?',
        (value, identity_value),
    )
    connection.commit()
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as captured:
        _binding_store(connection).persist(
            request=_binding_request(raw_object_id=raw.raw_object_id),
            selected_content=b"selected",
        )

    _assert_reason(captured, RawV2Reason.RAW_STATE_INVALID)
    assert _state_sha256(connection, root) == before


def test_locator_entity_unit_generation_resolve_permutations_are_exact(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    record = _binding_store(connection).persist(
        request=_binding_request(raw_object_id=raw.raw_object_id),
        selected_content=b"selected",
    )
    store = _binding_store(connection)
    requests = (
        RawBindingResolveRequest(project_id="project-a", locator_id=record.locator_id),
        RawBindingResolveRequest(
            project_id="project-a",
            derived_entity_id="entity-1",
            generation_id="generation-1",
        ),
        RawBindingResolveRequest(
            project_id="project-a",
            retrieval_unit_id="unit-1",
            generation_id="generation-1",
        ),
        RawBindingResolveRequest(
            project_id="project-a",
            derived_entity_id="entity-1",
            retrieval_unit_id="unit-1",
            generation_id="generation-1",
        ),
        RawBindingResolveRequest(
            project_id="project-a",
            locator_id=record.locator_id,
            derived_entity_id="entity-1",
            retrieval_unit_id="unit-1",
            generation_id="generation-1",
        ),
    )

    for request in requests:
        assert store.resolve(request=request) == replace(record, created=False)
    assert (
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                locator_id=record.locator_id,
                generation_id="wrong-generation",
            )
        )
        is None
    )


def test_generation_is_required_and_never_inferred_as_latest(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _binding_store(connection)
    older = store.persist(
        request=_binding_request(raw_object_id=raw.raw_object_id, generation_id="generation-1"),
        selected_content=b"selected",
    )
    newer = store.persist(
        request=_binding_request(
            raw_object_id=raw.raw_object_id,
            generation_id="generation-2",
            observed_at=LATER,
        ),
        selected_content=b"selected",
    )
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as captured:
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                derived_entity_id="entity-1",
            )
        )
    _assert_reason(captured, RawV2Reason.CANONICALIZATION_MISMATCH)
    assert store.resolve(
        request=RawBindingResolveRequest(
            project_id="project-a",
            derived_entity_id="entity-1",
            generation_id="generation-1",
        )
    ) == replace(older, created=False)
    assert store.resolve(
        request=RawBindingResolveRequest(
            project_id="project-a",
            derived_entity_id="entity-1",
            generation_id="generation-2",
        )
    ) == replace(newer, created=False)
    assert _state_sha256(connection, root) == before


def test_insufficient_multi_binding_resolution_is_typed_ambiguity(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _binding_store(connection)
    for unit, selected in (("unit-1", b"first"), ("unit-2", b"second")):
        store.persist(
            request=_binding_request(
                raw_object_id=raw.raw_object_id,
                retrieval_unit_id=unit,
            ),
            selected_content=selected,
        )
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as captured:
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                derived_entity_id="entity-1",
                generation_id="generation-1",
            )
        )

    _assert_reason(captured, RawV2Reason.BINDING_AMBIGUOUS)
    assert store.resolve(
        request=RawBindingResolveRequest(
            project_id="project-a",
            retrieval_unit_id="unit-2",
            generation_id="generation-1",
        )
    ) is not None
    assert _state_sha256(connection, root) == before


def test_same_unit_can_be_ambiguous_and_no_match_returns_none(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _binding_store(connection)
    for selector, selected in (
        ({"start_line": 1, "end_line": 2}, b"first"),
        ({"start_line": 3, "end_line": 4}, b"second"),
    ):
        store.persist(
            request=_binding_request(raw_object_id=raw.raw_object_id, selector=selector),
            selected_content=selected,
        )

    with pytest.raises(RawV2ContractError) as captured:
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                retrieval_unit_id="unit-1",
                generation_id="generation-1",
            )
        )
    _assert_reason(captured, RawV2Reason.BINDING_AMBIGUOUS)
    assert (
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                derived_entity_id="missing",
                generation_id="generation-1",
            )
        )
        is None
    )


def test_list_active_is_locator_ordered_and_excludes_invalidated_rows(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _binding_store(connection)
    records = [
        store.persist(
            request=_binding_request(
                raw_object_id=raw.raw_object_id,
                retrieval_unit_id=f"unit-{index}",
                observed_at=observed,
            ),
            selected_content=f"selected-{index}".encode(),
        )
        for index, observed in enumerate((LATER, OBSERVED, NOW), start=1)
    ]
    connection.execute(
        "UPDATE raw_evidence_bindings_v2 SET invalidated_at=?, invalidation_reason=? WHERE locator_id=?",
        (LATER, "fixture-invalidated", records[1].locator_id),
    )
    connection.commit()
    before = _state_sha256(connection, root)

    active = store.list_active(
        project_id="project-a",
        derived_entity_id="entity-1",
        generation_id="generation-1",
    )

    expected = sorted(
        (records[0], records[2]),
        key=lambda item: item.locator_id,
    )
    assert active == tuple(replace(item, created=False) for item in expected)
    assert (
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                locator_id=records[1].locator_id,
            )
        )
        is None
    )
    with pytest.raises(RawV2ContractError) as captured:
        store.persist(
            request=_binding_request(
                raw_object_id=raw.raw_object_id,
                retrieval_unit_id="unit-2",
                observed_at=OBSERVED,
            ),
            selected_content=b"selected-2",
        )
    _assert_reason(captured, RawV2Reason.BINDING_DIGEST_MISMATCH)
    assert _state_sha256(connection, root) == before


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("selector_json", "{}"),
        ("selector_sha256", "sha256:" + "1" * 64),
        ("raw_content_sha256", "sha256:" + "2" * 64),
        ("selected_content_sha256", "sha256:" + "3" * 64),
        ("parser_artifact_sha256", "sha256:" + "4" * 64),
        ("binding_json", "{}"),
        ("binding_sha256", "sha256:" + "5" * 64),
        ("created_at", "not-a-time"),
    ],
)
def test_resolve_detects_every_stored_binding_identity_tamper_without_writes(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    connection = _connection()
    root = tmp_path / column
    root.mkdir()
    raw = _active_object(connection, root)
    record = _binding_store(connection).persist(
        request=_binding_request(raw_object_id=raw.raw_object_id),
        selected_content=b"selected",
    )
    connection.execute(
        f'UPDATE raw_evidence_bindings_v2 SET "{column}"=? WHERE locator_id=?',
        (value, record.locator_id),
    )
    connection.commit()
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as captured:
        _binding_store(connection).resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                locator_id=record.locator_id,
            )
        )

    _assert_reason(captured, RawV2Reason.BINDING_DIGEST_MISMATCH)
    assert _state_sha256(connection, root) == before


def test_resolve_detects_locator_and_linked_object_tamper(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    record = _binding_store(connection).persist(
        request=_binding_request(raw_object_id=raw.raw_object_id),
        selected_content=b"selected",
    )
    tampered_locator = "raw-locator-v2:" + "6" * 64
    connection.execute(
        "UPDATE raw_evidence_bindings_v2 SET locator_id=? WHERE locator_id=?",
        (tampered_locator, record.locator_id),
    )
    connection.commit()
    before = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError) as locator_error:
        _binding_store(connection).resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                locator_id=tampered_locator,
            )
        )
    _assert_reason(locator_error, RawV2Reason.BINDING_DIGEST_MISMATCH)
    assert _state_sha256(connection, root) == before

    connection.execute(
        "UPDATE raw_evidence_bindings_v2 SET locator_id=? WHERE locator_id=?",
        (record.locator_id, tampered_locator),
    )
    connection.execute(
        "UPDATE raw_objects_v2 SET stable_version=? WHERE raw_object_id=?",
        ("tampered-stable", raw.raw_object_id),
    )
    connection.commit()
    before_object = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError) as object_error:
        _binding_store(connection).resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                locator_id=record.locator_id,
            )
        )
    _assert_reason(object_error, RawV2Reason.BINDING_DIGEST_MISMATCH)
    assert _state_sha256(connection, root) == before_object


def test_active_transaction_and_schema_drift_fail_before_binding_mutation(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    request = _binding_request(raw_object_id=raw.raw_object_id)
    connection.execute("BEGIN")
    before = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError) as transaction_error:
        _binding_store(connection).persist(
            request=request,
            selected_content=b"selected",
        )
    _assert_reason(transaction_error, RawV2Reason.RAW_STATE_INVALID)
    assert _state_sha256(connection, root) == before
    connection.rollback()

    connection.execute("DROP INDEX idx_bindings_v2_entity")
    before_drift = _state_sha256(connection, root)
    with pytest.raises(RawV2ContractError) as schema_error:
        _binding_store(connection).persist(
            request=request,
            selected_content=b"selected",
        )
    _assert_reason(schema_error, RawV2Reason.RAW_STATE_INVALID)
    assert _state_sha256(connection, root) == before_drift


def test_sqlite_failure_rolls_back_without_locator_or_other_side_effect(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    connection.execute(
        """CREATE TRIGGER fail_binding BEFORE INSERT ON raw_evidence_bindings_v2
           BEGIN SELECT RAISE(ABORT, 'injected binding failure'); END"""
    )
    connection.commit()
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as captured:
        _binding_store(connection).persist(
            request=_binding_request(raw_object_id=raw.raw_object_id),
            selected_content=b"selected",
        )

    _assert_reason(captured, RawV2Reason.RAW_STATE_INVALID)
    assert _state_sha256(connection, root) == before
    assert _count(connection, "raw_evidence_bindings_v2") == 0


def test_invalid_binding_inputs_fail_before_rows_or_blob_changes(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _binding_store(connection)
    before = _state_sha256(connection, root)

    with pytest.raises(RawV2ContractError) as byte_type:
        store.persist(
            request=_binding_request(raw_object_id=raw.raw_object_id),
            selected_content=bytearray(b"selected"),  # type: ignore[arg-type]
        )
    _assert_reason(byte_type, RawV2Reason.CANONICALIZATION_MISMATCH)
    with pytest.raises(RawV2ContractError) as too_large:
        store.persist(
            request=_binding_request(raw_object_id=raw.raw_object_id),
            selected_content=b"x" * 1_048_577,
        )
    _assert_reason(too_large, RawV2Reason.CONTRACT_SIZE_EXCEEDED)
    with pytest.raises(RawV2ContractError) as bad_selector:
        store.persist(
            request=_binding_request(
                raw_object_id=raw.raw_object_id,
                selector={"bad": 1.5},
            ),
            selected_content=b"selected",
        )
    _assert_reason(bad_selector, RawV2Reason.CANONICALIZATION_MISMATCH)
    with pytest.raises(RawV2ContractError):
        store.persist(
            request=_binding_request(
                raw_object_id=raw.raw_object_id,
                valid_from="2026-09-01T01:00:00Z",
                valid_to="2026-09-01T00:00:00Z",
            ),
            selected_content=b"selected",
        )
    with pytest.raises(RawV2ContractError) as empty_resolve:
        store.resolve(request=RawBindingResolveRequest(project_id="project-a"))
    _assert_reason(empty_resolve, RawV2Reason.CANONICALIZATION_MISMATCH)
    assert _state_sha256(connection, root) == before


def test_binding_write_conserves_v1_and_every_non_binding_v2_state(tmp_path: Path) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    connection.execute("CREATE TABLE legacy_bindings(id TEXT PRIMARY KEY, payload TEXT)")
    connection.execute("INSERT INTO legacy_bindings VALUES ('legacy-1', 'unchanged')")
    connection.execute("PRAGMA user_version = 92")
    connection.commit()
    raw = _active_object(connection, root)
    RawV2EventStore(connection=connection, policy=_policy(), clock=lambda: NOW).persist(
        request=RawEventWriteRequest(
            project_id="project-a",
            source_type="git",
            source_instance_id="repository-1",
            event_type="raw.persisted",
            mutation_id="mutation-1",
            source_object_id="object-1",
            source_version="version-1",
            source_acl_ref="source-public",
            raw_object_id=raw.raw_object_id,
            event_time=None,
            observed_at=OBSERVED,
            trace_id="trace-1",
            schema_version="source-event-schema-v1",
            status="persisted",
            metadata_projection={},
        )
    )
    legacy_before = connection.execute("SELECT * FROM legacy_bindings").fetchall()
    non_binding_before = {
        table: _database_projection(connection)[table]
        for table in RAW_V2_TABLES
        if table != "raw_evidence_bindings_v2"
    }
    tree_before = _tree_projection(root)

    _binding_store(connection).persist(
        request=_binding_request(raw_object_id=raw.raw_object_id),
        selected_content=b"selected",
    )

    assert connection.execute("SELECT * FROM legacy_bindings").fetchall() == legacy_before
    assert connection.execute("PRAGMA user_version").fetchone() == (92,)
    assert {
        table: _database_projection(connection)[table]
        for table in RAW_V2_TABLES
        if table != "raw_evidence_bindings_v2"
    } == non_binding_before
    assert _tree_projection(root) == tree_before


def test_two_connections_serialize_to_one_exact_binding(tmp_path: Path) -> None:
    database = tmp_path / "bindings.sqlite3"
    root = tmp_path / "blobs"
    root.mkdir()
    bootstrap = _connection(database)
    raw = _active_object(bootstrap, root)
    bootstrap.close()
    request = _binding_request(raw_object_id=raw.raw_object_id)
    barrier = threading.Barrier(2)

    def write() -> RawBindingRecord:
        connection = sqlite3.connect(database, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        store = _binding_store(connection)
        barrier.wait(timeout=5.0)
        try:
            return store.persist(request=request, selected_content=b"selected")
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: write(), range(2)))

    verified = sqlite3.connect(database)
    assert len({item.locator_id for item in results}) == 1
    assert sorted(item.created for item in results) == [False, True]
    assert _count(verified, "raw_evidence_bindings_v2") == 1
    assert _count(verified, "raw_objects_v2") == 1
    assert _count(verified, "raw_blobs_v2") == 1
    verified.close()


def test_selected_content_change_creates_an_explicit_second_binding_not_overwrite(
    tmp_path: Path,
) -> None:
    connection = _connection()
    root = tmp_path / "blobs"
    root.mkdir()
    raw = _active_object(connection, root)
    store = _binding_store(connection)
    first = store.persist(
        request=_binding_request(raw_object_id=raw.raw_object_id),
        selected_content=b"first",
    )
    second = store.persist(
        request=_binding_request(raw_object_id=raw.raw_object_id),
        selected_content=b"second",
    )

    assert first.locator_id != second.locator_id
    assert _count(connection, "raw_evidence_bindings_v2") == 2
    with pytest.raises(RawV2ContractError) as captured:
        store.resolve(
            request=RawBindingResolveRequest(
                project_id="project-a",
                derived_entity_id="entity-1",
                retrieval_unit_id="unit-1",
                generation_id="generation-1",
            )
        )
    _assert_reason(captured, RawV2Reason.BINDING_AMBIGUOUS)
