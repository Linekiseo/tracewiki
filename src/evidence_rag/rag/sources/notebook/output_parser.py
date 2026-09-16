"""Safe projection of Notebook stream/display/error outputs."""

from __future__ import annotations

import html
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import unquote

from ....security import redact_secrets
from .contracts import NotebookArtifactType, canonical_sha256

NOTEBOOK_OUTPUT_PARSER_VERSION = "notebook-output-parser-v2"

_SCRIPT_RE = re.compile(r"(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_POSIX_ABS_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9:])/(?:Users|home|private|tmp|var|etc|opt|root)"
    r"(?:/[^\s\"'`<>]+)+"
)
_WINDOWS_ABS_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/][^\s\"'`<>]+")
_UNC_RE = re.compile(r"(?i)(?<!:)(?:\\\\|//)[^\\/\s\"'`<>]+[\\/][^\s\"'`<>]+")
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")
_IDENTIFIER_RE = re.compile(r"[^A-Za-z0-9._:@-]+")


@dataclass(frozen=True, slots=True)
class ParsedNotebookOutput:
    artifact_type: NotebookArtifactType
    mime_types: tuple[str, ...]
    text: str
    error_name: str | None
    error_value: str | None
    binary_omitted: bool
    metric_confirmed: bool
    content_sha256: str
    diagnostics: tuple[str, ...]


def _text(value: object) -> str:
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    return "" if value is None else str(value)


def _contains_path(value: str) -> bool:
    decoded = unicodedata.normalize("NFKC", value)
    for _ in range(4):
        if (
            _POSIX_ABS_RE.search(decoded)
            or _WINDOWS_ABS_RE.search(decoded)
            or _UNC_RE.search(decoded)
        ):
            return True
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return False


def sanitize_notebook_derived_text_v2(
    value: object,
    *,
    limit: int = 100_000,
) -> tuple[str, tuple[str, ...]]:
    raw = _text(value)
    redacted, findings = redact_secrets(raw)
    diagnostics = set(findings)
    if _contains_path(redacted):
        redacted = "[REDACTED_PATH]"
        diagnostics.add("absolute-path-redacted")
    cleaned = unicodedata.normalize("NFC", redacted)
    cleaned = "".join(
        character
        if not unicodedata.category(character).startswith("C") or character in {"\n", "\r", "\t"}
        else " "
        for character in cleaned
    )
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
        diagnostics.add("output-truncated")
    return cleaned, tuple(sorted(diagnostics))


def _safe_error_name(value: object) -> str:
    cleaned = _IDENTIFIER_RE.sub("-", str(value or "NotebookError")).strip(".-")
    return (cleaned or "NotebookError")[:120]


def _safe_html(value: object) -> str:
    return html.unescape(_TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", _text(value))))


def parse_notebook_output_v2(output: Mapping[str, object]) -> ParsedNotebookOutput:
    content_sha256 = canonical_sha256(output)
    output_type = str(output.get("output_type") or "display_data")
    data = output.get("data") if isinstance(output.get("data"), Mapping) else {}
    mime_types = tuple(
        sorted(
            key.casefold()
            for key in data
            if isinstance(key, str) and _MIME_RE.fullmatch(key.casefold())
        )
    )
    binary_omitted = any(
        mime.startswith("image/") or mime in {"application/octet-stream", "application/pdf"}
        for mime in mime_types
    )
    diagnostics: set[str] = set()
    if binary_omitted:
        diagnostics.add("binary-payload-omitted")
    if output_type == "error":
        error_name = _safe_error_name(output.get("ename"))
        error_value, value_findings = sanitize_notebook_derived_text_v2(
            output.get("evalue"), limit=20_000
        )
        text, text_findings = sanitize_notebook_derived_text_v2(
            "\n".join(
                part
                for part in (
                    error_name,
                    error_value,
                    _text(output.get("traceback")),
                )
                if part
            )
        )
        diagnostics.update(value_findings)
        diagnostics.update(text_findings)
        return ParsedNotebookOutput(
            artifact_type=NotebookArtifactType.ERROR,
            mime_types=mime_types,
            text=text,
            error_name=error_name,
            error_value=error_value,
            binary_omitted=False,
            metric_confirmed=False,
            content_sha256=content_sha256,
            diagnostics=tuple(sorted(diagnostics)),
        )
    if output_type == "stream":
        text, findings = sanitize_notebook_derived_text_v2(output.get("text"))
        diagnostics.update(findings)
        artifact_type = NotebookArtifactType.STREAM
    else:
        raw_text: object = ""
        if "text/plain" in data:
            raw_text = data["text/plain"]
        elif "application/json" in data:
            try:
                raw_text = json.dumps(
                    data["application/json"],
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            except (TypeError, ValueError):
                raw_text = ""
                diagnostics.add("json-output-unavailable")
        elif "text/html" in data:
            raw_text = _safe_html(data["text/html"])
            diagnostics.add("html-sanitized")
        text, findings = sanitize_notebook_derived_text_v2(raw_text)
        diagnostics.update(findings)
        artifact_type = (
            NotebookArtifactType.BINARY_OMITTED
            if binary_omitted and not text
            else NotebookArtifactType.EXECUTE_RESULT
            if output_type == "execute_result"
            else NotebookArtifactType.DISPLAY
        )
    return ParsedNotebookOutput(
        artifact_type=artifact_type,
        mime_types=mime_types,
        text=text,
        error_name=None,
        error_value=None,
        binary_omitted=binary_omitted,
        metric_confirmed=False,
        content_sha256=content_sha256,
        diagnostics=tuple(sorted(diagnostics)),
    )


__all__ = [
    "NOTEBOOK_OUTPUT_PARSER_VERSION",
    "ParsedNotebookOutput",
    "parse_notebook_output_v2",
    "sanitize_notebook_derived_text_v2",
]
