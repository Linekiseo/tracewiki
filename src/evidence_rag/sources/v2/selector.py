"""Deterministic bounded selector execution for authenticated raw V2 bytes.

The module is intentionally storage-, transport-, policy-, and runtime-neutral.
Its private execution seam is called only by the shared managed reader so raw
authentication happens before selection and a fresh authority recheck happens
after selection.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn

from .contracts import (
    SIZE_LIMITS,
    RawSelectorPayload,
    RawV2ContractError,
    RawV2Reason,
    SizeLimit,
    SourceDomain,
    canonical_json_bytes,
    derive_selector_sha256,
    ensure_utf8_size,
    exact_bytes_sha256,
    strict_json_loads,
    validate_sha256_digest,
    verify_canonical_json_bytes,
)

if TYPE_CHECKING:
    from .reader import RawManagedReadResult

RAW_V2_SELECTOR_EXECUTOR_VERSION = "raw-v2-selector-executor-v1"
REVIEWED_SELECTOR_KINDS = (
    "whole_object_v2",
    "utf8_range_v2",
    "json_pointer_v2",
    "notebook_cell_v2",
    "document_span_v2",
    "document_table_v2",
    "document_figure_v2",
    "manifest_member_v2",
)


@dataclass(frozen=True, slots=True)
class RawSelectedReadResult:
    """Authenticated selected bytes without raw or physical-storage disclosure."""

    project_id: str
    locator_id: str
    binding_sha256: str
    raw_object_id: str
    source_domain: SourceDomain
    source_type: str
    source_instance_id: str
    source_object_id: str
    source_version: str
    stable_version: str
    generation_id: str
    media_type: str
    raw_content_sha256: str
    selector_kind: str
    selector_sha256: str
    selected_content_sha256: str
    selected_byte_length: int
    parser_artifact_sha256: str | None
    selected_bytes: bytes


def _fail(reason: RawV2Reason, detail: str) -> NoReturn:
    raise RawV2ContractError(reason, detail)


def _selector_invalid(detail: str, error: Exception | None = None) -> NoReturn:
    failure = RawV2ContractError(RawV2Reason.SELECTOR_INVALID, detail)
    if error is None:
        raise failure
    raise failure from error


def _exact_keys(
    value: object,
    expected: set[str],
    *,
    label: str,
) -> dict[str, object]:
    if type(value) is not dict:
        _selector_invalid(f"{label} must be an exact JSON object")
    projected = value
    if set(projected) != expected:
        _selector_invalid(f"{label} fields are not the reviewed exact schema")
    return projected


def _exact_int(value: object, *, label: str) -> int:
    if type(value) is not int:
        _selector_invalid(f"{label} must be an exact integer")
    return value


def _exact_text(
    value: object,
    *,
    label: str,
    allow_empty: bool = False,
    limit: SizeLimit = SizeLimit.PORTABLE_ID,
) -> str:
    if type(value) is not str:
        _selector_invalid(f"{label} must be an exact string")
    try:
        normalized = unicodedata.normalize("NFC", value)
        normalized.encode("utf-8")
    except UnicodeError as error:
        _selector_invalid(f"{label} must be valid UTF-8", error)
    if normalized != value:
        _selector_invalid(f"{label} must already be NFC")
    if not allow_empty and not normalized:
        _selector_invalid(f"{label} must be non-empty")
    if any(ord(character) < 32 for character in normalized):
        _selector_invalid(f"{label} contains a C0 control")
    try:
        ensure_utf8_size(normalized, limit, label=label)
    except RawV2ContractError as error:
        _selector_invalid(f"{label} exceeds its reviewed byte limit", error)
    return normalized


def _exact_digest(value: object, *, label: str) -> str:
    try:
        return validate_sha256_digest(value)
    except RawV2ContractError as error:
        _selector_invalid(f"{label} must be one canonical SHA-256 digest", error)


def _is_json_media(media_type: str) -> bool:
    return media_type == "application/json" or media_type.endswith("+json")


def _require_json_media(media_type: str, *, label: str) -> None:
    if not _is_json_media(media_type):
        _fail(RawV2Reason.UNSUPPORTED_MEDIA, f"{label} requires reviewed JSON media")


def _require_text_media(media_type: str, *, label: str) -> None:
    if not media_type.startswith("text/"):
        _fail(RawV2Reason.UNSUPPORTED_MEDIA, f"{label} requires reviewed text media")


def _strict_json(raw_bytes: bytes, *, canonical: bool, label: str) -> object:
    try:
        if canonical:
            return verify_canonical_json_bytes(raw_bytes, allow_none=True)
        return strict_json_loads(raw_bytes, allow_none=True)
    except (RawV2ContractError, TypeError, ValueError) as error:
        _selector_invalid(f"{label} is not reviewed strict JSON", error)


def _parser_digest(selector_digest: str, raw_digest: str | None) -> None:
    if raw_digest is None:
        _fail(
            RawV2Reason.PARSE_ARTIFACT_MISSING,
            "selector requires a stored reviewed parser artifact",
        )
    try:
        canonical_raw_digest = validate_sha256_digest(raw_digest)
    except RawV2ContractError as error:
        raise RawV2ContractError(
            RawV2Reason.PARSE_ARTIFACT_MISMATCH,
            "stored parser artifact digest is malformed",
        ) from error
    if selector_digest != canonical_raw_digest:
        _fail(
            RawV2Reason.PARSE_ARTIFACT_MISMATCH,
            "selector and binding parser artifact digests differ",
        )


def _json_pointer_token(token: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            output.append(character)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            _selector_invalid("JSON pointer contains a non-RFC-6901 escape")
        output.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(output)


def _token_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor == len(text):
            break
        start = cursor
        while cursor < len(text) and not text[cursor].isspace():
            cursor += 1
        spans.append((start, cursor))
    return tuple(spans)


class RawV2SelectorExecutor:
    """Validate and execute stored selectors with one explicit output ceiling."""

    def __init__(self, *, max_selected_bytes: int) -> None:
        if type(max_selected_bytes) is not int or max_selected_bytes <= 0:
            _fail(
                RawV2Reason.CANONICALIZATION_MISMATCH,
                "max_selected_bytes must be an exact positive integer",
            )
        global_limit = SIZE_LIMITS[SizeLimit.SELECTED_OUTPUT]
        if max_selected_bytes > global_limit:
            _fail(
                RawV2Reason.CONTRACT_SIZE_EXCEEDED,
                "selected output ceiling exceeds the frozen global maximum",
            )
        self._max_selected_bytes = max_selected_bytes

    def _execute(self, *, raw: RawManagedReadResult) -> bytes:
        from .reader import RawManagedReadResult

        if type(raw) is not RawManagedReadResult:
            _selector_invalid("selector input is not an authenticated raw result")
        payload = self._selector_payload(raw)
        selector = payload.selector
        kind = payload.selector_kind
        if kind == "whole_object_v2":
            selected = self._whole_object(selector=selector, raw=raw)
        elif kind == "utf8_range_v2":
            selected = self._utf8_range(selector=selector, raw=raw)
        elif kind == "json_pointer_v2":
            selected = self._json_pointer(selector=selector, raw=raw)
        elif kind == "notebook_cell_v2":
            selected = self._notebook_cell(selector=selector, raw=raw)
        elif kind == "document_span_v2":
            selected = self._document_span(selector=selector, raw=raw)
        elif kind == "document_table_v2":
            selected = self._document_table(selector=selector, raw=raw)
        elif kind == "document_figure_v2":
            selected = self._document_figure(selector=selector, raw=raw)
        elif kind == "manifest_member_v2":
            selected = self._manifest_member(selector=selector, raw=raw)
        else:  # pragma: no cover - guarded by the exact registry check
            raise AssertionError("reviewed selector dispatch is incomplete")
        return self._verify_selected(raw=raw, selected=selected)

    def _selector_payload(self, raw: RawManagedReadResult) -> RawSelectorPayload:
        if type(raw.selector_json) is not str:
            _selector_invalid("stored selector envelope must be exact text")
        try:
            encoded = raw.selector_json.encode("utf-8")
        except UnicodeError as error:
            _selector_invalid("stored selector envelope is not UTF-8", error)
        try:
            ensure_utf8_size(encoded, SizeLimit.CANONICAL_JSON, label="selector envelope")
        except RawV2ContractError as error:
            if error.reason is RawV2Reason.CONTRACT_SIZE_EXCEEDED:
                raise
            _selector_invalid("stored selector envelope size is invalid", error)
        try:
            parsed = verify_canonical_json_bytes(encoded, allow_none=True)
            payload = RawSelectorPayload.model_validate(parsed)
        except (RawV2ContractError, TypeError, ValueError) as error:
            _selector_invalid("stored selector envelope is not exact canonical authority", error)
        if payload.selector_kind not in REVIEWED_SELECTOR_KINDS:
            _selector_invalid("stored selector kind is not in the reviewed V2 union")
        if type(raw.selector_kind) is not str or payload.selector_kind != raw.selector_kind:
            _selector_invalid("stored selector envelope and binding kind differ")
        try:
            stored_digest = validate_sha256_digest(raw.selector_sha256)
            expected_digest = derive_selector_sha256(payload)
        except (RawV2ContractError, TypeError, ValueError) as error:
            _selector_invalid("stored selector digest is malformed", error)
        if stored_digest != expected_digest:
            _selector_invalid("stored selector digest differs from canonical envelope")
        return payload

    def _verify_selected(self, *, raw: RawManagedReadResult, selected: object) -> bytes:
        if type(selected) is not bytes:
            _fail(RawV2Reason.SELECTED_DIGEST_MISMATCH, "selector output is not exact bytes")
        if len(selected) > self._max_selected_bytes:
            _fail(
                RawV2Reason.CONTRACT_SIZE_EXCEEDED,
                "selected output exceeds the trusted selected-byte ceiling",
            )
        try:
            expected_digest = validate_sha256_digest(raw.selected_content_sha256)
        except RawV2ContractError as error:
            raise RawV2ContractError(
                RawV2Reason.SELECTED_DIGEST_MISMATCH,
                "stored selected digest is malformed",
            ) from error
        if exact_bytes_sha256(selected) != expected_digest:
            _fail(
                RawV2Reason.SELECTED_DIGEST_MISMATCH,
                "selected bytes differ from stored selected digest",
            )
        return selected

    def _result(
        self,
        *,
        raw: RawManagedReadResult,
        selected: bytes,
    ) -> RawSelectedReadResult:
        selected = self._verify_selected(raw=raw, selected=selected)
        return RawSelectedReadResult(
            project_id=raw.project_id,
            locator_id=raw.locator_id,
            binding_sha256=raw.binding_sha256,
            raw_object_id=raw.raw_object_id,
            source_domain=raw.source_domain,
            source_type=raw.source_type,
            source_instance_id=raw.source_instance_id,
            source_object_id=raw.source_object_id,
            source_version=raw.source_version,
            stable_version=raw.stable_version,
            generation_id=raw.generation_id,
            media_type=raw.media_type,
            raw_content_sha256=raw.raw_content_sha256,
            selector_kind=raw.selector_kind,
            selector_sha256=raw.selector_sha256,
            selected_content_sha256=raw.selected_content_sha256,
            selected_byte_length=len(selected),
            parser_artifact_sha256=raw.parser_artifact_sha256,
            selected_bytes=selected,
        )

    def _whole_object(
        self,
        *,
        selector: dict[str, object],
        raw: RawManagedReadResult,
    ) -> bytes:
        _exact_keys(selector, set(), label="whole-object selector")
        if len(raw.raw_bytes) > self._max_selected_bytes:
            _fail(
                RawV2Reason.CONTRACT_SIZE_EXCEEDED,
                "whole-object output exceeds the selected-byte ceiling",
            )
        return raw.raw_bytes

    def _utf8_range(
        self,
        *,
        selector: dict[str, object],
        raw: RawManagedReadResult,
    ) -> bytes:
        value = _exact_keys(
            selector,
            {"start_byte", "end_byte", "start_line", "end_line", "newline_policy"},
            label="UTF-8 range selector",
        )
        start = _exact_int(value["start_byte"], label="start byte")
        end = _exact_int(value["end_byte"], label="end byte")
        start_line = _exact_int(value["start_line"], label="start line")
        end_line = _exact_int(value["end_line"], label="end line")
        if value["newline_policy"] != "preserve_v1":
            _selector_invalid("UTF-8 range newline policy is not preserve_v1")
        _require_text_media(raw.media_type, label="UTF-8 range selector")
        try:
            raw.raw_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RawV2ContractError(
                RawV2Reason.UNSUPPORTED_MEDIA,
                "text media is not strict UTF-8",
            ) from error
        if start < 0 or end < start or end > len(raw.raw_bytes) or start_line < 1 or end_line < 1:
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "UTF-8 range bounds are invalid")
        try:
            raw.raw_bytes[:start].decode("utf-8")
            raw.raw_bytes[:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise RawV2ContractError(
                RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                "UTF-8 range splits a code-point boundary",
            ) from error
        expected_start_line = 1 + raw.raw_bytes[:start].count(b"\n")
        expected_end_line = 1 + raw.raw_bytes[:end].count(b"\n")
        if start_line != expected_start_line or end_line != expected_end_line:
            _fail(
                RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                "UTF-8 byte and line boundary coordinates disagree",
            )
        if end - start > self._max_selected_bytes:
            _fail(
                RawV2Reason.CONTRACT_SIZE_EXCEEDED,
                "UTF-8 range exceeds the selected-byte ceiling",
            )
        return raw.raw_bytes[start:end]

    def _json_pointer(
        self,
        *,
        selector: dict[str, object],
        raw: RawManagedReadResult,
    ) -> bytes:
        value = _exact_keys(
            selector,
            {"pointer", "canonical_json_policy"},
            label="JSON pointer selector",
        )
        pointer = _exact_text(
            value["pointer"],
            label="JSON pointer",
            allow_empty=True,
            limit=SizeLimit.SOURCE_OBJECT_ID,
        )
        if value["canonical_json_policy"] != "canonical_json_v1":
            _selector_invalid("JSON pointer canonical policy is not canonical_json_v1")
        _require_json_media(raw.media_type, label="JSON pointer selector")
        selected = _strict_json(raw.raw_bytes, canonical=True, label="JSON pointer raw object")
        if pointer:
            if not pointer.startswith("/"):
                _selector_invalid("JSON pointer must be empty or begin with slash")
            for raw_token in pointer.split("/")[1:]:
                token = _json_pointer_token(raw_token)
                if type(selected) is dict:
                    if token not in selected:
                        _fail(
                            RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                            "JSON pointer object key is absent",
                        )
                    selected = selected[token]
                elif type(selected) is list:
                    if token == "0":
                        index = 0
                    elif token and token[0] != "0" and all("0" <= item <= "9" for item in token):
                        if len(token) > len(str(len(selected))) + 1:
                            _fail(
                                RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                                "JSON pointer array index is outside the value",
                            )
                        index = int(token)
                    else:
                        _selector_invalid("JSON pointer array index is not canonical")
                    if index >= len(selected):
                        _fail(
                            RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                            "JSON pointer array index is outside the value",
                        )
                    selected = selected[index]
                else:
                    _fail(
                        RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                        "JSON pointer traverses beyond a scalar",
                    )
        return canonical_json_bytes(selected, allow_none=True)

    def _notebook_cell(
        self,
        *,
        selector: dict[str, object],
        raw: RawManagedReadResult,
    ) -> bytes:
        keys = frozenset(selector)
        source_keys = {"cell_index", "stable_cell_id", "source_ordinal"}
        output_keys = {"cell_index", "stable_cell_id", "output_ordinal"}
        if keys not in {frozenset(source_keys), frozenset(output_keys)}:
            _selector_invalid("Notebook selector must choose exactly one source/output ordinal")
        cell_index = _exact_int(selector["cell_index"], label="Notebook cell index")
        _exact_text(selector["stable_cell_id"], label="Notebook stable cell ID")
        ordinal_name = "source_ordinal" if "source_ordinal" in selector else "output_ordinal"
        ordinal = _exact_int(selector[ordinal_name], label=f"Notebook {ordinal_name}")
        if cell_index < 0 or ordinal < 0:
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "Notebook selector index is negative")
        if raw.media_type not in {"application/x-ipynb+json", "application/json"}:
            _fail(
                RawV2Reason.UNSUPPORTED_MEDIA,
                "Notebook selector requires reviewed Notebook JSON media",
            )
        notebook = _strict_json(raw.raw_bytes, canonical=False, label="Notebook raw object")
        if type(notebook) is not dict or type(notebook.get("cells")) is not list:
            _selector_invalid("Notebook raw object has no exact cells array")
        cells = notebook["cells"]
        if cell_index >= len(cells):
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "Notebook cell index is outside cells")
        cell = cells[cell_index]
        if type(cell) is not dict:
            _selector_invalid("Notebook cell must be an exact JSON object")
        if ordinal_name == "source_ordinal":
            source = cell.get("source")
            if type(source) is str:
                if ordinal != 0:
                    _fail(
                        RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                        "Notebook string source has only ordinal zero",
                    )
                return source.encode("utf-8")
            if type(source) is not list:
                _selector_invalid("Notebook cell source must be a string or string array")
            if ordinal >= len(source):
                _fail(
                    RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                    "Notebook source ordinal is outside fragments",
                )
            fragment = source[ordinal]
            if type(fragment) is not str:
                _selector_invalid("Notebook source fragment must be an exact string")
            return fragment.encode("utf-8")
        outputs = cell.get("outputs")
        if type(outputs) is not list:
            _selector_invalid("Notebook cell outputs must be an exact array")
        if ordinal >= len(outputs):
            _fail(
                RawV2Reason.SELECTOR_OUT_OF_BOUNDS,
                "Notebook output ordinal is outside outputs",
            )
        return canonical_json_bytes(outputs[ordinal], allow_none=True)

    def _document_span(
        self,
        *,
        selector: dict[str, object],
        raw: RawManagedReadResult,
    ) -> bytes:
        value = _exact_keys(
            selector,
            {
                "page",
                "section_path",
                "start_char",
                "end_char",
                "start_token",
                "end_token",
                "parse_artifact_sha256",
            },
            label="document span selector",
        )
        page = _exact_int(value["page"], label="document page")
        start_char = _exact_int(value["start_char"], label="document start char")
        end_char = _exact_int(value["end_char"], label="document end char")
        start_token = _exact_int(value["start_token"], label="document start token")
        end_token = _exact_int(value["end_token"], label="document end token")
        section_path = value["section_path"]
        if type(section_path) is not list:
            _selector_invalid("document section path must be an exact string array")
        for item in section_path:
            _exact_text(item, label="document section path item", limit=SizeLimit.SOURCE_OBJECT_ID)
        parser_digest = _exact_digest(
            value["parse_artifact_sha256"],
            label="document span parser digest",
        )
        _parser_digest(parser_digest, raw.parser_artifact_sha256)
        if page < 1:
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "document page is below one")
        if page != 1 or section_path:
            _fail(
                RawV2Reason.PARSE_ARTIFACT_MISSING,
                "parser-backed document span requires T1.7.1 authority",
            )
        _require_text_media(raw.media_type, label="direct document span")
        try:
            text = raw.raw_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RawV2ContractError(
                RawV2Reason.UNSUPPORTED_MEDIA,
                "direct document text is not strict UTF-8",
            ) from error
        spans = _token_spans(text)
        if (
            start_char < 0
            or end_char < start_char
            or end_char > len(text)
            or start_token < 0
            or end_token < start_token
            or end_token > len(spans)
        ):
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "document span is outside text")
        if start_token == end_token:
            boundary = spans[start_token][0] if start_token < len(spans) else len(text)
            expected_start = expected_end = boundary
        else:
            expected_start = spans[start_token][0]
            expected_end = spans[end_token - 1][1]
        if start_char != expected_start or end_char != expected_end:
            _selector_invalid("document character and token spans disagree")
        return text[start_char:end_char].encode("utf-8")

    def _document_table(
        self,
        *,
        selector: dict[str, object],
        raw: RawManagedReadResult,
    ) -> bytes:
        value = _exact_keys(
            selector,
            {
                "page",
                "table_id",
                "row_semantic_key",
                "column_semantic_key",
                "parse_artifact_sha256",
            },
            label="document table selector",
        )
        page = _exact_int(value["page"], label="document table page")
        _exact_text(value["table_id"], label="document table ID")
        _exact_text(
            value["row_semantic_key"],
            label="document table row semantic key",
            limit=SizeLimit.SOURCE_OBJECT_ID,
        )
        _exact_text(
            value["column_semantic_key"],
            label="document table column semantic key",
            limit=SizeLimit.SOURCE_OBJECT_ID,
        )
        parser_digest = _exact_digest(
            value["parse_artifact_sha256"],
            label="document table parser digest",
        )
        if page < 1:
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "document table page is below one")
        _parser_digest(parser_digest, raw.parser_artifact_sha256)
        _fail(
            RawV2Reason.PARSE_ARTIFACT_MISSING,
            "document table execution requires T1.7.1 parser authority",
        )

    def _document_figure(
        self,
        *,
        selector: dict[str, object],
        raw: RawManagedReadResult,
    ) -> bytes:
        base = {"page", "figure_id", "parse_artifact_sha256"}
        keys = frozenset(selector)
        if keys not in {frozenset(base | {"caption"}), frozenset(base | {"region"})}:
            _selector_invalid("document figure must choose exactly one caption/region")
        page = _exact_int(selector["page"], label="document figure page")
        _exact_text(selector["figure_id"], label="document figure ID")
        if "caption" in selector:
            _exact_text(
                selector["caption"],
                label="document figure caption",
                limit=SizeLimit.SOURCE_OBJECT_ID,
            )
        else:
            region = selector["region"]
            if type(region) is not list or len(region) != 4:
                _selector_invalid("document figure region must have four integers")
            x0, y0, x1, y1 = (
                _exact_int(item, label="document figure region coordinate") for item in region
            )
            if min(x0, y0) < 0 or x0 >= x1 or y0 >= y1:
                _selector_invalid("document figure region geometry is invalid")
        parser_digest = _exact_digest(
            selector["parse_artifact_sha256"],
            label="document figure parser digest",
        )
        if page < 1:
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "document figure page is below one")
        _parser_digest(parser_digest, raw.parser_artifact_sha256)
        _fail(
            RawV2Reason.PARSE_ARTIFACT_MISSING,
            "document figure execution requires T1.7.1 parser authority",
        )

    def _manifest_member(
        self,
        *,
        selector: dict[str, object],
        raw: RawManagedReadResult,
    ) -> bytes:
        value = _exact_keys(
            selector,
            {"member_id", "member_sha256", "ordinal"},
            label="manifest member selector",
        )
        member_id = _exact_text(value["member_id"], label="manifest member ID")
        member_digest = _exact_digest(
            value["member_sha256"],
            label="manifest member digest",
        )
        ordinal = _exact_int(value["ordinal"], label="manifest member ordinal")
        if ordinal < 0:
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "manifest ordinal is negative")
        _require_json_media(raw.media_type, label="manifest member selector")
        manifest = _strict_json(raw.raw_bytes, canonical=True, label="raw manifest")
        manifest = _exact_keys(
            manifest,
            {"schema_version", "members"},
            label="raw manifest",
        )
        if manifest["schema_version"] != "raw-manifest-v2":
            _selector_invalid("raw manifest schema version is not raw-manifest-v2")
        members = manifest["members"]
        if type(members) is not list:
            _selector_invalid("raw manifest members must be an exact array")
        if ordinal >= len(members):
            _fail(RawV2Reason.SELECTOR_OUT_OF_BOUNDS, "manifest ordinal is outside members")
        descriptor = _exact_keys(
            members[ordinal],
            {"member_id", "member_sha256", "member"},
            label="raw manifest member descriptor",
        )
        descriptor_id = _exact_text(
            descriptor["member_id"],
            label="raw manifest descriptor member ID",
        )
        descriptor_digest = _exact_digest(
            descriptor["member_sha256"],
            label="raw manifest descriptor member digest",
        )
        selected = canonical_json_bytes(descriptor["member"], allow_none=True)
        actual_digest = exact_bytes_sha256(selected)
        if (
            descriptor_id != member_id
            or descriptor_digest != member_digest
            or actual_digest != member_digest
        ):
            _fail(
                RawV2Reason.SELECTED_DIGEST_MISMATCH,
                "manifest member identity or digest differs",
            )
        return selected


__all__ = [
    "RAW_V2_SELECTOR_EXECUTOR_VERSION",
    "REVIEWED_SELECTOR_KINDS",
    "RawSelectedReadResult",
    "RawV2SelectorExecutor",
]
