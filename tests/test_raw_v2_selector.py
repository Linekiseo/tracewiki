from __future__ import annotations

import inspect
import json
import os
import sqlite3
import stat
from dataclasses import FrozenInstanceError, dataclass, fields, replace
from pathlib import Path

import pytest

import evidence_rag.sources.v2.selector as selector_module
from evidence_rag.sources.v2.contracts import (
    RawV2ContractError,
    RawV2Reason,
    SourceDomain,
    canonical_json_bytes,
    exact_bytes_sha256,
)
from evidence_rag.sources.v2.policy import RawV2ProjectPolicyAdapter
from evidence_rag.sources.v2.reader import (
    RawManagedReadRequest,
    RawManagedReadResult,
    RawV2ManagedReader,
)
from evidence_rag.sources.v2.schema import initialize_raw_v2_schema
from evidence_rag.sources.v2.selector import (
    RAW_V2_SELECTOR_EXECUTOR_VERSION,
    REVIEWED_SELECTOR_KINDS,
    RawSelectedReadResult,
    RawV2SelectorExecutor,
)
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
PARSER_DIGEST = exact_bytes_sha256(b"reviewed-parser-artifact")


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
        return "public" if source_acl_ref == "source-public" else None

    def resolve_requester_acl_refs(
        self,
        *,
        project_id: str,
        principal_id: str,
    ) -> tuple[str, ...] | None:
        if project_id != "project-a" or principal_id != "public-reader":
            return None
        return ()


def _policy() -> RawV2ProjectPolicyAdapter:
    return RawV2ProjectPolicyAdapter(authority=_Authority())


@dataclass(slots=True)
class _Fixture:
    connection: sqlite3.Connection
    root: Path
    payload: bytes
    binding: RawBindingRecord
    request: RawManagedReadRequest
    reader: RawV2ManagedReader
    executor: RawV2SelectorExecutor


def _fixture(
    tmp_path: Path,
    *,
    payload: bytes,
    selector_kind: str,
    selector: dict[str, object],
    selected_content: bytes,
    media_type: str = "text/plain",
    parser_artifact_sha256: str | None = None,
    max_selected_bytes: int = 1_048_576,
    database: Path | None = None,
) -> _Fixture:
    connection = sqlite3.connect(":memory:" if database is None else database)
    initialize_raw_v2_schema(connection)
    root = tmp_path / "blobs"
    root.mkdir(parents=True, exist_ok=True)
    raw = RawV2ObjectStore(
        connection=connection,
        blob_root=root,
        policy=_policy(),
        clock=lambda: NOW,
    ).persist_bytes(
        request=RawObjectWriteRequest(
            project_id="project-a",
            source_type="git",
            source_instance_id="repository-1",
            source_object_id="src/app.py",
            source_version="commit-1",
            stable_version="blob-1",
            source_acl_ref="source-public",
            media_type=media_type,
            adapter_version="git-adapter-v2",
            schema_version="raw-object-v2",
            metadata_projection={},
            observed_at=OBSERVED,
        ),
        payload=payload,
    )
    binding = RawV2BindingStore(
        connection=connection,
        policy=_policy(),
        clock=lambda: NOW,
    ).persist(
        request=RawBindingWriteRequest(
            project_id="project-a",
            raw_object_id=raw.raw_object_id,
            source_acl_ref="source-public",
            source_version="commit-1",
            stable_version="blob-1",
            generation_id="generation-1",
            derived_entity_id="entity-1",
            retrieval_unit_id="unit-1",
            derivation_kind="source_slice",
            derivation_version="derivation-v1",
            selector_kind=selector_kind,
            selector=selector,
            parser_artifact_sha256=parser_artifact_sha256,
            valid_from="2026-08-31T23:58:00Z",
            valid_to=None,
            observed_at=OBSERVED,
            adapter_version="git-adapter-v2",
            schema_version="raw-binding-v2",
        ),
        selected_content=selected_content,
    )
    request = RawManagedReadRequest(
        project_id="project-a",
        principal_id="public-reader",
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
            max_raw_bytes=max(1, len(payload) + 1),
        ),
        executor=RawV2SelectorExecutor(max_selected_bytes=max_selected_bytes),
    )


def _read_selected(fixture: _Fixture) -> RawSelectedReadResult:
    return fixture.reader.read_selected(
        request=fixture.request,
        selector_executor=fixture.executor,
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


def _database_dump(connection: sqlite3.Connection) -> str:
    return "\n".join(connection.iterdump())


def _tree_projection(root: Path) -> tuple[tuple[object, ...], ...]:
    projection: list[tuple[object, ...]] = []
    for directory, names, files in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        for name in sorted([*names, *files]):
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


def _raw_result(fixture: _Fixture) -> RawManagedReadResult:
    return fixture.reader.read(request=fixture.request)


def test_selector_api_is_frozen_bounded_and_integrated_only() -> None:
    assert RAW_V2_SELECTOR_EXECUTOR_VERSION == "raw-v2-selector-executor-v1"
    assert REVIEWED_SELECTOR_KINDS == (
        "whole_object_v2",
        "utf8_range_v2",
        "json_pointer_v2",
        "notebook_cell_v2",
        "document_span_v2",
        "document_table_v2",
        "document_figure_v2",
        "manifest_member_v2",
    )
    assert [field.name for field in fields(RawSelectedReadResult)] == [
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
        "selector_kind",
        "selector_sha256",
        "selected_content_sha256",
        "selected_byte_length",
        "parser_artifact_sha256",
        "selected_bytes",
    ]
    prohibited = {
        "raw_bytes",
        "selector_json",
        "path",
        "storage_path",
        "storage_key",
        "acl_ref",
        "acl_refs",
        "visibility_partition_sha256",
    }
    assert prohibited.isdisjoint(field.name for field in fields(RawSelectedReadResult))
    assert list(inspect.signature(RawV2SelectorExecutor).parameters) == ["max_selected_bytes"]
    assert list(inspect.signature(RawV2ManagedReader.read_selected).parameters) == [
        "self",
        "request",
        "selector_executor",
    ]
    assert [
        name
        for name, value in inspect.getmembers(RawV2SelectorExecutor, inspect.isfunction)
        if not name.startswith("_")
    ] == []


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "1024", None])
def test_executor_rejects_nonexact_or_nonpositive_ceiling(value: object) -> None:
    _assert_reason(
        RawV2Reason.CANONICALIZATION_MISMATCH,
        RawV2SelectorExecutor,
        max_selected_bytes=value,
    )


def test_executor_rejects_ceiling_above_global_limit() -> None:
    _assert_reason(
        RawV2Reason.CONTRACT_SIZE_EXCEEDED,
        RawV2SelectorExecutor,
        max_selected_bytes=1_048_577,
    )


@pytest.mark.parametrize("payload", [b"", b"\x00\xffbinary\n"])
def test_whole_object_selects_exact_empty_or_binary_bytes(tmp_path: Path, payload: bytes) -> None:
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="whole_object_v2",
        selector={},
        selected_content=payload,
        media_type="application/octet-stream",
    )
    result = _read_selected(fixture)
    assert result.selected_bytes == payload
    assert result.selected_byte_length == len(payload)
    assert result.selected_content_sha256 == exact_bytes_sha256(payload)
    assert not hasattr(result, "raw_bytes")
    with pytest.raises(FrozenInstanceError):
        result.selected_bytes = b"changed"  # type: ignore[misc]


def test_selected_output_ceiling_and_digest_mismatch_return_no_result(tmp_path: Path) -> None:
    oversized = _fixture(
        tmp_path / "oversized",
        payload=b"abcdef",
        selector_kind="whole_object_v2",
        selector={},
        selected_content=b"abcdef",
        max_selected_bytes=5,
    )
    _assert_reason(RawV2Reason.CONTRACT_SIZE_EXCEEDED, _read_selected, oversized)

    mismatched = _fixture(
        tmp_path / "mismatch",
        payload=b"abcdef",
        selector_kind="whole_object_v2",
        selector={},
        selected_content=b"different",
    )
    _assert_reason(RawV2Reason.SELECTED_DIGEST_MISMATCH, _read_selected, mismatched)


def test_utf8_range_preserves_multibyte_crlf_and_boundary_lines(tmp_path: Path) -> None:
    payload = "α\r\nbeta\n终".encode()
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="utf8_range_v2",
        selector={
            "start_byte": 4,
            "end_byte": 9,
            "start_line": 2,
            "end_line": 3,
            "newline_policy": "preserve_v1",
        },
        selected_content=b"beta\n",
    )
    assert _read_selected(fixture).selected_bytes == b"beta\n"

    empty = _fixture(
        tmp_path / "empty",
        payload=payload,
        selector_kind="utf8_range_v2",
        selector={
            "start_byte": len(payload),
            "end_byte": len(payload),
            "start_line": 3,
            "end_line": 3,
            "newline_policy": "preserve_v1",
        },
        selected_content=b"",
    )
    assert _read_selected(empty).selected_bytes == b""


@pytest.mark.parametrize(
    "selector",
    [
        {
            "start_byte": -1,
            "end_byte": 1,
            "start_line": 1,
            "end_line": 1,
            "newline_policy": "preserve_v1",
        },
        {
            "start_byte": 2,
            "end_byte": 1,
            "start_line": 1,
            "end_line": 1,
            "newline_policy": "preserve_v1",
        },
        {
            "start_byte": 0,
            "end_byte": 99,
            "start_line": 1,
            "end_line": 1,
            "newline_policy": "preserve_v1",
        },
        {
            "start_byte": 1,
            "end_byte": 2,
            "start_line": 1,
            "end_line": 1,
            "newline_policy": "preserve_v1",
        },
        {
            "start_byte": 0,
            "end_byte": 2,
            "start_line": 2,
            "end_line": 2,
            "newline_policy": "preserve_v1",
        },
    ],
)
def test_utf8_range_bounds_codepoint_and_line_disagreement_fail_closed(
    tmp_path: Path,
    selector: dict[str, object],
) -> None:
    fixture = _fixture(
        tmp_path,
        payload="α\nb".encode(),
        selector_kind="utf8_range_v2",
        selector=selector,
        selected_content=b"",
    )
    _assert_reason(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, _read_selected, fixture)


@pytest.mark.parametrize(
    ("payload", "media_type"),
    [(b"\xff", "text/plain"), (b"plain", "image/png")],
)
def test_utf8_range_rejects_invalid_utf8_or_wrong_media(
    tmp_path: Path,
    payload: bytes,
    media_type: str,
) -> None:
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="utf8_range_v2",
        selector={
            "start_byte": 0,
            "end_byte": len(payload),
            "start_line": 1,
            "end_line": 1,
            "newline_policy": "preserve_v1",
        },
        selected_content=payload,
        media_type=media_type,
    )
    _assert_reason(RawV2Reason.UNSUPPORTED_MEDIA, _read_selected, fixture)


@pytest.mark.parametrize(
    ("pointer", "expected"),
    [
        ("", b'{"a/b":{"~key":[null,{"v":1}]}}'),
        ("/a~1b/~0key/0", b"null"),
        ("/a~1b/~0key/1/v", b"1"),
        ("/a~1b", b'{"~key":[null,{"v":1}]}'),
    ],
)
def test_json_pointer_rfc6901_and_canonical_output(
    tmp_path: Path,
    pointer: str,
    expected: bytes,
) -> None:
    payload = canonical_json_bytes({"a/b": {"~key": [None, {"v": 1}]}}, allow_none=True)
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="json_pointer_v2",
        selector={"pointer": pointer, "canonical_json_policy": "canonical_json_v1"},
        selected_content=expected,
        media_type="application/json",
    )
    assert _read_selected(fixture).selected_bytes == expected


@pytest.mark.parametrize("pointer", ["/missing", "/a/2"])
def test_json_pointer_missing_path_is_out_of_bounds(tmp_path: Path, pointer: str) -> None:
    payload = canonical_json_bytes({"a": [1]})
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="json_pointer_v2",
        selector={"pointer": pointer, "canonical_json_policy": "canonical_json_v1"},
        selected_content=b"",
        media_type="application/json",
    )
    _assert_reason(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, _read_selected, fixture)


@pytest.mark.parametrize(
    "pointer",
    ["not-a-pointer", "/bad~2escape", "/a/01", "/a/-", "/a/١"],
)
def test_json_pointer_malformed_pointer_or_index_is_invalid(tmp_path: Path, pointer: str) -> None:
    payload = canonical_json_bytes({"a": [1, 2]})
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="json_pointer_v2",
        selector={"pointer": pointer, "canonical_json_policy": "canonical_json_v1"},
        selected_content=b"",
        media_type="application/json",
    )
    _assert_reason(RawV2Reason.SELECTOR_INVALID, _read_selected, fixture)


@pytest.mark.parametrize(
    ("payload", "media_type"),
    [
        (b'{"a": 1}', "application/json"),
        (b'{"a":1,"a":2}', "application/json"),
        (b'{"a":1}', "text/plain"),
    ],
)
def test_json_pointer_requires_canonical_unambiguous_json_media(
    tmp_path: Path,
    payload: bytes,
    media_type: str,
) -> None:
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="json_pointer_v2",
        selector={"pointer": "/a", "canonical_json_policy": "canonical_json_v1"},
        selected_content=b"1",
        media_type=media_type,
    )
    expected = (
        RawV2Reason.UNSUPPORTED_MEDIA
        if media_type == "text/plain"
        else RawV2Reason.SELECTOR_INVALID
    )
    _assert_reason(expected, _read_selected, fixture)


def test_notebook_cell_selects_source_fragment_and_canonical_output(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "cells": [
                {
                    "id": "cell-a",
                    "source": ["a = 1\n", "a"],
                    "outputs": [{"value": 1, "kind": "display"}, "plain"],
                }
            ]
        },
        ensure_ascii=False,
        indent=2,
    ).encode()
    source = _fixture(
        tmp_path / "source",
        payload=payload,
        selector_kind="notebook_cell_v2",
        selector={"cell_index": 0, "stable_cell_id": "cell-a", "source_ordinal": 1},
        selected_content=b"a",
        media_type="application/x-ipynb+json",
    )
    assert _read_selected(source).selected_bytes == b"a"

    output_bytes = canonical_json_bytes({"value": 1, "kind": "display"})
    output = _fixture(
        tmp_path / "output",
        payload=payload,
        selector_kind="notebook_cell_v2",
        selector={"cell_index": 0, "stable_cell_id": "cell-a", "output_ordinal": 0},
        selected_content=output_bytes,
        media_type="application/json",
    )
    assert _read_selected(output).selected_bytes == output_bytes


def test_notebook_string_source_is_one_fragment(tmp_path: Path) -> None:
    payload = canonical_json_bytes({"cells": [{"source": "print(1)\n", "outputs": []}]})
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="notebook_cell_v2",
        selector={"cell_index": 0, "stable_cell_id": "stable-a", "source_ordinal": 0},
        selected_content=b"print(1)\n",
        media_type="application/x-ipynb+json",
    )
    assert _read_selected(fixture).selected_bytes == b"print(1)\n"


@pytest.mark.parametrize(
    "selector",
    [
        {"cell_index": 1, "stable_cell_id": "cell-a", "source_ordinal": 0},
        {"cell_index": 0, "stable_cell_id": "cell-a", "source_ordinal": 1},
        {"cell_index": 0, "stable_cell_id": "cell-a", "output_ordinal": 0},
    ],
)
def test_notebook_cell_and_ordinal_bounds_fail_closed(
    tmp_path: Path,
    selector: dict[str, object],
) -> None:
    payload = canonical_json_bytes({"cells": [{"source": ["x"], "outputs": []}]})
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="notebook_cell_v2",
        selector=selector,
        selected_content=b"",
        media_type="application/x-ipynb+json",
    )
    _assert_reason(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, _read_selected, fixture)


def test_document_span_direct_text_requires_matching_char_and_token_coordinates(
    tmp_path: Path,
) -> None:
    payload = "alpha βeta gamma".encode()
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="document_span_v2",
        selector={
            "page": 1,
            "section_path": [],
            "start_char": 0,
            "end_char": 5,
            "start_token": 0,
            "end_token": 1,
            "parse_artifact_sha256": PARSER_DIGEST,
        },
        selected_content=b"alpha",
        media_type="text/plain",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    assert _read_selected(fixture).selected_bytes == b"alpha"

    empty = _fixture(
        tmp_path / "empty",
        payload=payload,
        selector_kind="document_span_v2",
        selector={
            "page": 1,
            "section_path": [],
            "start_char": 6,
            "end_char": 6,
            "start_token": 1,
            "end_token": 1,
            "parse_artifact_sha256": PARSER_DIGEST,
        },
        selected_content=b"",
        media_type="text/plain",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    assert _read_selected(empty).selected_bytes == b""


@pytest.mark.parametrize(
    "selector_update",
    [
        {"start_char": -1},
        {"end_char": 999},
        {"start_token": 2, "end_token": 1},
        {"end_token": 99},
    ],
)
def test_document_span_bounds_fail_closed(
    tmp_path: Path,
    selector_update: dict[str, object],
) -> None:
    selector: dict[str, object] = {
        "page": 1,
        "section_path": [],
        "start_char": 0,
        "end_char": 5,
        "start_token": 0,
        "end_token": 1,
        "parse_artifact_sha256": PARSER_DIGEST,
    }
    selector.update(selector_update)
    fixture = _fixture(
        tmp_path,
        payload=b"alpha beta",
        selector_kind="document_span_v2",
        selector=selector,
        selected_content=b"alpha",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    _assert_reason(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, _read_selected, fixture)


def test_document_span_coordinate_parser_media_and_artifact_boundaries(tmp_path: Path) -> None:
    mismatched_coordinates = _fixture(
        tmp_path / "coordinates",
        payload=b"alpha beta",
        selector_kind="document_span_v2",
        selector={
            "page": 1,
            "section_path": [],
            "start_char": 0,
            "end_char": 4,
            "start_token": 0,
            "end_token": 1,
            "parse_artifact_sha256": PARSER_DIGEST,
        },
        selected_content=b"alph",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    _assert_reason(RawV2Reason.SELECTOR_INVALID, _read_selected, mismatched_coordinates)

    wrong_digest = _fixture(
        tmp_path / "digest",
        payload=b"alpha beta",
        selector_kind="document_span_v2",
        selector={
            "page": 1,
            "section_path": [],
            "start_char": 0,
            "end_char": 5,
            "start_token": 0,
            "end_token": 1,
            "parse_artifact_sha256": exact_bytes_sha256(b"other"),
        },
        selected_content=b"alpha",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    _assert_reason(RawV2Reason.PARSE_ARTIFACT_MISMATCH, _read_selected, wrong_digest)

    parser_backed = _fixture(
        tmp_path / "page",
        payload=b"alpha beta",
        selector_kind="document_span_v2",
        selector={
            "page": 2,
            "section_path": ["Methods"],
            "start_char": 0,
            "end_char": 5,
            "start_token": 0,
            "end_token": 1,
            "parse_artifact_sha256": PARSER_DIGEST,
        },
        selected_content=b"alpha",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    _assert_reason(RawV2Reason.PARSE_ARTIFACT_MISSING, _read_selected, parser_backed)

    wrong_media = _fixture(
        tmp_path / "media",
        payload=b"%PDF",
        selector_kind="document_span_v2",
        selector={
            "page": 1,
            "section_path": [],
            "start_char": 0,
            "end_char": 4,
            "start_token": 0,
            "end_token": 1,
            "parse_artifact_sha256": PARSER_DIGEST,
        },
        selected_content=b"%PDF",
        media_type="application/pdf",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    _assert_reason(RawV2Reason.UNSUPPORTED_MEDIA, _read_selected, wrong_media)


@pytest.mark.parametrize(
    ("selector_kind", "selector"),
    [
        (
            "document_table_v2",
            {
                "page": 1,
                "table_id": "table-1",
                "row_semantic_key": "row-a",
                "column_semantic_key": "column-b",
                "parse_artifact_sha256": PARSER_DIGEST,
            },
        ),
        (
            "document_figure_v2",
            {
                "page": 1,
                "figure_id": "figure-1",
                "caption": "Figure caption",
                "parse_artifact_sha256": PARSER_DIGEST,
            },
        ),
        (
            "document_figure_v2",
            {
                "page": 1,
                "figure_id": "figure-1",
                "region": [0, 1, 20, 30],
                "parse_artifact_sha256": PARSER_DIGEST,
            },
        ),
    ],
)
def test_parser_backed_table_and_figure_validate_then_fail_typed_missing(
    tmp_path: Path,
    selector_kind: str,
    selector: dict[str, object],
) -> None:
    fixture = _fixture(
        tmp_path,
        payload=b"%PDF",
        selector_kind=selector_kind,
        selector=selector,
        selected_content=b"",
        media_type="application/pdf",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    _assert_reason(RawV2Reason.PARSE_ARTIFACT_MISSING, _read_selected, fixture)


@pytest.mark.parametrize(
    "selector",
    [
        {
            "page": 1,
            "figure_id": "figure-1",
            "caption": "caption",
            "region": [0, 0, 1, 1],
            "parse_artifact_sha256": PARSER_DIGEST,
        },
        {
            "page": 1,
            "figure_id": "figure-1",
            "region": [0, 0, 0, 1],
            "parse_artifact_sha256": PARSER_DIGEST,
        },
    ],
)
def test_document_figure_discriminator_and_region_geometry_are_exact(
    tmp_path: Path,
    selector: dict[str, object],
) -> None:
    fixture = _fixture(
        tmp_path,
        payload=b"%PDF",
        selector_kind="document_figure_v2",
        selector=selector,
        selected_content=b"",
        media_type="application/pdf",
        parser_artifact_sha256=PARSER_DIGEST,
    )
    _assert_reason(RawV2Reason.SELECTOR_INVALID, _read_selected, fixture)


@pytest.mark.parametrize("member", [{}, {"kind": "event", "value": [1, "two"]}])
def test_manifest_member_selects_exact_canonical_member(
    tmp_path: Path,
    member: dict[str, object],
) -> None:
    selected = canonical_json_bytes(member)
    member_digest = exact_bytes_sha256(selected)
    manifest = {
        "schema_version": "raw-manifest-v2",
        "members": [
            {
                "member_id": "member-a",
                "member_sha256": member_digest,
                "member": member,
            }
        ],
    }
    payload = canonical_json_bytes(manifest)
    fixture = _fixture(
        tmp_path,
        payload=payload,
        selector_kind="manifest_member_v2",
        selector={"member_id": "member-a", "member_sha256": member_digest, "ordinal": 0},
        selected_content=selected,
        media_type="application/vnd.evidence-rag.raw-manifest-v2+json",
    )
    assert _read_selected(fixture).selected_bytes == selected


def test_manifest_member_bounds_identity_and_digest_fail_closed(tmp_path: Path) -> None:
    member = {"value": 1}
    selected = canonical_json_bytes(member)
    digest = exact_bytes_sha256(selected)
    payload = canonical_json_bytes(
        {
            "schema_version": "raw-manifest-v2",
            "members": [{"member_id": "member-a", "member_sha256": digest, "member": member}],
        }
    )
    for name, selector, reason in (
        (
            "ordinal",
            {"member_id": "member-a", "member_sha256": digest, "ordinal": 1},
            RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
        ),
        (
            "identity",
            {"member_id": "member-b", "member_sha256": digest, "ordinal": 0},
            RawV2Reason.SELECTED_DIGEST_MISMATCH,
        ),
        (
            "digest",
            {
                "member_id": "member-a",
                "member_sha256": exact_bytes_sha256(b"other"),
                "ordinal": 0,
            },
            RawV2Reason.SELECTED_DIGEST_MISMATCH,
        ),
    ):
        fixture = _fixture(
            tmp_path / name,
            payload=payload,
            selector_kind="manifest_member_v2",
            selector=selector,
            selected_content=selected,
            media_type="application/json",
        )
        _assert_reason(reason, _read_selected, fixture)


def test_manifest_member_rejects_noncanonical_manifest_and_wrong_media(tmp_path: Path) -> None:
    digest = exact_bytes_sha256(b"{}")
    noncanonical = b'{"schema_version": "raw-manifest-v2", "members": []}'
    invalid = _fixture(
        tmp_path / "invalid",
        payload=noncanonical,
        selector_kind="manifest_member_v2",
        selector={"member_id": "member-a", "member_sha256": digest, "ordinal": 0},
        selected_content=b"{}",
        media_type="application/json",
    )
    _assert_reason(RawV2Reason.SELECTOR_INVALID, _read_selected, invalid)

    wrong_media = _fixture(
        tmp_path / "media",
        payload=canonical_json_bytes({"schema_version": "raw-manifest-v2", "members": []}),
        selector_kind="manifest_member_v2",
        selector={"member_id": "member-a", "member_sha256": digest, "ordinal": 0},
        selected_content=b"{}",
        media_type="text/plain",
    )
    _assert_reason(RawV2Reason.UNSUPPORTED_MEDIA, _read_selected, wrong_media)


@pytest.mark.parametrize(
    ("selector_kind", "selector"),
    [
        ("line_range", {"start_line": 1, "end_line": 2}),
        ("whole_object", {}),
        ("unknown_v2", {}),
        ("whole_object_v2", {"extra": True}),
        (
            "utf8_range_v2",
            {
                "start_byte": True,
                "end_byte": 1,
                "start_line": 1,
                "end_line": 1,
                "newline_policy": "preserve_v1",
            },
        ),
        (
            "notebook_cell_v2",
            {
                "cell_index": 0,
                "stable_cell_id": "cell-a",
                "source_ordinal": 0,
                "output_ordinal": 0,
            },
        ),
    ],
)
def test_union_rejects_legacy_unknown_extra_bool_and_bad_discriminator(
    tmp_path: Path,
    selector_kind: str,
    selector: dict[str, object],
) -> None:
    fixture = _fixture(
        tmp_path,
        payload=b"x",
        selector_kind=selector_kind,
        selector=selector,
        selected_content=b"x",
    )
    _assert_reason(RawV2Reason.SELECTOR_INVALID, _read_selected, fixture)


def test_stored_selector_envelope_kind_and_digest_tamper_are_invalid(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        payload=b"x",
        selector_kind="whole_object_v2",
        selector={},
        selected_content=b"x",
    )
    raw = _raw_result(fixture)
    malformed = replace(raw, selector_json='{ "selector":{},"selector_kind":"whole_object_v2"}')
    _assert_reason(RawV2Reason.SELECTOR_INVALID, fixture.executor._execute, raw=malformed)
    wrong_kind = replace(raw, selector_kind="utf8_range_v2")
    _assert_reason(RawV2Reason.SELECTOR_INVALID, fixture.executor._execute, raw=wrong_kind)
    wrong_digest = replace(raw, selector_sha256="sha256:" + "0" * 64)
    _assert_reason(RawV2Reason.SELECTOR_INVALID, fixture.executor._execute, raw=wrong_digest)


def test_tombstone_after_selector_is_stopped_before_selected_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "selector-race.sqlite3"
    fixture = _fixture(
        tmp_path,
        payload=b"selected",
        selector_kind="whole_object_v2",
        selector={},
        selected_content=b"selected",
        database=database,
    )
    writer = sqlite3.connect(database)
    original = RawV2SelectorExecutor._execute
    tombstoned = False

    def select_then_tombstone(
        self: RawV2SelectorExecutor,
        *,
        raw: RawManagedReadResult,
    ) -> bytes:
        nonlocal tombstoned
        selected = original(self, raw=raw)
        RawV2TombstoneStore(
            connection=writer,
            policy=_policy(),
            clock=lambda: NOW,
        ).tombstone(
            request=RawTombstoneRequest(
                project_id="project-a",
                raw_object_id=raw.raw_object_id,
                source_acl_ref="source-public",
                mutation_id="delete-after-selector",
                reason_code="source_deleted",
                actor_digest=ACTOR_DIGEST,
                event_time=None,
                observed_at=NOW,
                trace_id="trace-delete-after-selector",
                schema_version="raw-tombstone-v1",
            )
        )
        tombstoned = True
        return selected

    monkeypatch.setattr(RawV2SelectorExecutor, "_execute", select_then_tombstone)
    _assert_reason(RawV2Reason.BINDING_DIGEST_MISMATCH, _read_selected, fixture)
    assert tombstoned is True
    writer.close()


def test_selected_read_is_zero_mutation_and_raw_read_remains_unchanged(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        payload=b"unchanged",
        selector_kind="whole_object_v2",
        selector={},
        selected_content=b"unchanged",
    )
    before_db = _database_dump(fixture.connection)
    before_tree = _tree_projection(fixture.root)
    raw = fixture.reader.read(request=fixture.request)
    selected = _read_selected(fixture)
    assert raw.raw_bytes == b"unchanged"
    assert raw.selector_json.endswith('"selector_kind":"whole_object_v2"}')
    assert selected.selected_bytes == b"unchanged"
    assert _database_dump(fixture.connection) == before_db
    assert _tree_projection(fixture.root) == before_tree


def test_selector_module_has_no_operational_fallback_capability() -> None:
    source = inspect.getsource(selector_module)
    forbidden = (
        "sqlite3",
        "subprocess",
        "requests",
        "httpx",
        "socket",
        "urllib",
        "Path(",
        "open(",
        "os.environ",
        "re.compile",
    )
    assert [token for token in forbidden if token in source] == []
