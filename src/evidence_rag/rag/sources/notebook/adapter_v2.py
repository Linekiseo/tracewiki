"""Pure Notebook V2 adapter.

The adapter never executes notebook code and never reads a path.  Callers provide immutable
bytes plus governed scope explicitly.  Raw content identity is retained by digest while
derived text is sanitized before it can enter the isolated store.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from typing import Any
from urllib.parse import unquote

from ....security import redact_secrets
from .code_analyzer import analyze_notebook_code_v2
from .contracts import (
    NOTEBOOK_ADAPTER_VERSION,
    NOTEBOOK_CONTRACT_VERSION,
    NOTEBOOK_SCHEMA_VERSION,
    NotebookArtifact,
    NotebookArtifactType,
    NotebookCellExecution,
    NotebookCellType,
    NotebookCellVersion,
    NotebookExecution,
    NotebookExecutionStatus,
    NotebookIdentityConfidence,
    NotebookParameter,
    NotebookPublication,
    NotebookRevision,
    NotebookScope,
    NotebookTemplate,
    canonical_sha256,
)
from .dependency_graph import (
    build_notebook_dependency_graph_v2,
    build_notebook_structural_edges_v2,
)
from .execution_state import (
    NotebookCellExecutionInput,
    project_notebook_execution_state_v2,
)
from .output_parser import parse_notebook_output_v2
from .parameter_parser import parse_notebook_parameters_v2
from .unit_builder import build_notebook_retrieval_units_v2

MAX_NOTEBOOK_BYTES = 50_000_000
MAX_CELLS = 10_000
MAX_OUTPUTS_PER_CELL = 10_000

_NATIVE_CELL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PORTABLE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_POSIX_ABS_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9:])/(?:Users|home|private|tmp|var|etc|opt|root)"
    r"(?:/[^\s\"'`<>]+)+"
)
_WINDOWS_ABS_RE = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/][^\s\"'`<>]+")
_UNC_RE = re.compile(r"(?i)(?<!:)(?:\\\\|//)[^\\/\s\"'`<>]+[\\/][^\s\"'`<>]+")


class NotebookAdapterError(ValueError):
    """Raised for malformed or unsafe Notebook inputs."""


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join((prefix, *parts)).encode("utf-8")
    return f"{prefix}-" + hashlib.sha256(payload).hexdigest()


def _safe_identifier(value: object, *, fallback: str) -> str:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    cleaned = _SAFE_NAME_RE.sub("-", raw).strip(".-_")[:120]
    if not cleaned or not cleaned[0].isalnum():
        cleaned = fallback
    return cleaned


def _text(value: object) -> str:
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _contains_absolute_path(value: str) -> bool:
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


def _sanitize_derived_text(value: object, *, limit: int = 100_000) -> tuple[str, tuple[str, ...]]:
    raw = _text(value)
    redacted, findings = redact_secrets(raw)
    finding_names = set(findings)
    if _contains_absolute_path(redacted):
        finding_names.add("absolute_path")
        redacted = "[REDACTED_PATH]"
    cleaned = unicodedata.normalize("NFC", redacted)
    cleaned = "".join(
        character
        if not unicodedata.category(character).startswith("C") or character in {"\n", "\r", "\t"}
        else " "
        for character in cleaned
    )
    if len(cleaned) > limit:
        cleaned = cleaned[:limit]
        finding_names.add("truncated")
    return cleaned, tuple(sorted(finding_names))


def _json_no_nonfinite(payload: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise NotebookAdapterError(f"non-finite JSON constant is forbidden: {value}")

    try:
        return json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotebookAdapterError("notebook is not canonical UTF-8 JSON") from exc


def _timestamp(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("+00:00") or raw.endswith("Z"):
        return raw
    return None


def _cell_type(value: object) -> NotebookCellType:
    normalized = str(value or "raw").casefold()
    if normalized == "code":
        return NotebookCellType.CODE
    if normalized == "markdown":
        return NotebookCellType.MARKDOWN
    return NotebookCellType.RAW


class NotebookAdapterV2:
    """Build one immutable revision/execution publication from notebook bytes."""

    adapter_version = NOTEBOOK_ADAPTER_VERSION

    def build_publication(
        self,
        *,
        payload: bytes,
        project_id: str,
        acl_ref: str,
        generation_id: str,
        source_key: str,
        source_version: str,
        execution_key: str,
    ) -> NotebookPublication:
        if not payload or len(payload) > MAX_NOTEBOOK_BYTES:
            raise NotebookAdapterError("notebook payload size is outside the supported range")
        if not _PORTABLE_KEY_RE.fullmatch(source_key) or source_key in {".", ".."}:
            raise NotebookAdapterError("source key must be a portable filename, not a path")
        root = _json_no_nonfinite(payload)
        if not isinstance(root, Mapping) or not isinstance(root.get("cells"), list):
            raise NotebookAdapterError("notebook must contain a cells array")
        cells_raw = root["cells"]
        if len(cells_raw) > MAX_CELLS:
            raise NotebookAdapterError("notebook contains too many cells")
        if any(not isinstance(item, Mapping) for item in cells_raw):
            raise NotebookAdapterError("every notebook cell must be an object")
        nbformat = root.get("nbformat")
        nbformat_minor = root.get("nbformat_minor", 0)
        if type(nbformat) is not int or nbformat < 1:
            raise NotebookAdapterError("nbformat must be a positive integer")
        if type(nbformat_minor) is not int or nbformat_minor < 0:
            raise NotebookAdapterError("nbformat_minor must be a non-negative integer")

        scope = NotebookScope(
            project_id=project_id,
            acl_ref=acl_ref,
            generation_id=generation_id,
        )
        metadata = root.get("metadata") if isinstance(root.get("metadata"), Mapping) else {}
        language_info = (
            metadata.get("language_info")
            if isinstance(metadata.get("language_info"), Mapping)
            else {}
        )
        kernelspec = (
            metadata.get("kernelspec") if isinstance(metadata.get("kernelspec"), Mapping) else {}
        )
        language = _safe_identifier(language_info.get("name"), fallback="unknown")
        kernel = _safe_identifier(kernelspec.get("name"), fallback="unknown")
        name = _safe_identifier(source_key.rsplit(".", 1)[0], fallback="notebook")
        template_id = _id("nbtmpl", project_id, source_key)
        template_locator = f"notebook://{project_id}/template/{template_id}"
        template = NotebookTemplate(
            template_id=template_id,
            scope=scope,
            source_key=source_key,
            name=name,
            language=language,
            kernel_name=kernel,
            locator=template_locator,
        )

        native_ids = [
            str(item.get("id"))
            for item in cells_raw
            if item.get("id") is not None and _NATIVE_CELL_ID_RE.fullmatch(str(item.get("id")))
        ]
        duplicate_native = {value for value, count in Counter(native_ids).items() if count > 1}
        if duplicate_native:
            raise NotebookAdapterError("duplicate native cell id is not a valid revision")

        revision_cells: list[dict[str, Any]] = []
        stable_ids: list[str] = []
        for ordinal, raw_cell in enumerate(cells_raw):
            source_raw = _text(raw_cell.get("source"))
            source_digest = _sha256_bytes(source_raw.encode("utf-8"))
            native_id = raw_cell.get("id")
            if native_id is not None and _NATIVE_CELL_ID_RE.fullmatch(str(native_id)):
                stable_cell_id = _id("nbcell", template_id, str(native_id))
                confidence = NotebookIdentityConfidence.NATIVE
                stored_native_id = str(native_id)
            else:
                stable_cell_id = _id(
                    "nbcell",
                    template_id,
                    str(_cell_type(raw_cell.get("cell_type"))),
                    source_digest,
                    str(ordinal),
                )
                confidence = NotebookIdentityConfidence.DERIVED
                stored_native_id = None
            stable_ids.append(stable_cell_id)
            cell_metadata = (
                raw_cell.get("metadata") if isinstance(raw_cell.get("metadata"), Mapping) else {}
            )
            tags = tuple(
                sorted(
                    {
                        _safe_identifier(
                            tag,
                            fallback="tag-" + hashlib.sha256(str(tag).encode()).hexdigest()[:16],
                        )
                        for tag in cell_metadata.get("tags", [])
                        if isinstance(tag, str)
                    }
                )
            )
            revision_cells.append(
                {
                    "display_order": ordinal,
                    "cell_type": str(_cell_type(raw_cell.get("cell_type"))),
                    "source_sha256": source_digest,
                    "stable_cell_id": stable_cell_id,
                    "native_cell_id": stored_native_id,
                    "identity_confidence": str(confidence),
                    "tags": tags,
                }
            )
        revision_content_sha = canonical_sha256(
            {
                "nbformat": nbformat,
                "nbformat_minor": nbformat_minor,
                "cells": revision_cells,
            }
        )
        revision_id = _id(
            "nbrev",
            template_id,
            scope.acl_ref,
            scope.generation_id,
            revision_content_sha,
        )
        revision_locator = f"{template_locator}/revision/{revision_id}"

        cell_versions: list[NotebookCellVersion] = []
        for ordinal, (raw_cell, stable_id, revision_cell) in enumerate(
            zip(cells_raw, stable_ids, revision_cells, strict=True)
        ):
            source_raw = _text(raw_cell.get("source"))
            safe_source, _ = _sanitize_derived_text(source_raw)
            cell_version_id = _id(
                "nbcellv",
                revision_id,
                stable_id,
                revision_cell["source_sha256"],
            )
            cell_versions.append(
                NotebookCellVersion(
                    cell_version_id=cell_version_id,
                    stable_cell_id=stable_id,
                    revision_id=revision_id,
                    scope=scope,
                    display_order=ordinal,
                    cell_type=revision_cell["cell_type"],
                    source=safe_source,
                    source_sha256=revision_cell["source_sha256"],
                    tags=revision_cell["tags"],
                    native_cell_id=revision_cell["native_cell_id"],
                    identity_confidence=revision_cell["identity_confidence"],
                    locator=f"{revision_locator}/cell/{stable_id}",
                )
            )
        revision = NotebookRevision(
            revision_id=revision_id,
            template_id=template_id,
            scope=scope,
            source_version=source_version,
            content_sha256=revision_content_sha,
            metadata_sha256=canonical_sha256(metadata),
            nbformat=nbformat,
            nbformat_minor=nbformat_minor,
            cell_version_ids=tuple(item.cell_version_id for item in cell_versions),
            locator=revision_locator,
        )

        execution_facts: list[dict[str, Any]] = []
        parsed_outputs_by_cell = []
        execution_inputs: list[NotebookCellExecutionInput] = []
        for display_order, raw_cell in enumerate(cells_raw):
            count = raw_cell.get("execution_count")
            valid_count = count if type(count) is int and count >= 0 else None
            outputs = raw_cell.get("outputs") if isinstance(raw_cell.get("outputs"), list) else []
            if len(outputs) > MAX_OUTPUTS_PER_CELL or any(
                not isinstance(output, Mapping) for output in outputs
            ):
                raise NotebookAdapterError("cell outputs are malformed or exceed the limit")
            parsed_outputs = tuple(parse_notebook_output_v2(output) for output in outputs)
            parsed_outputs_by_cell.append(parsed_outputs)
            execution_inputs.append(
                NotebookCellExecutionInput(
                    display_order=display_order,
                    cell_type=str(_cell_type(raw_cell.get("cell_type"))),
                    execution_count=valid_count,
                    has_outputs=bool(outputs),
                    has_error=any(
                        item.artifact_type == NotebookArtifactType.ERROR for item in parsed_outputs
                    ),
                )
            )
            execution_facts.append(
                {
                    "execution_count": valid_count,
                    "outputs": [item.content_sha256 for item in parsed_outputs],
                }
            )
        papermill = (
            metadata.get("papermill") if isinstance(metadata.get("papermill"), Mapping) else {}
        )
        execution_content_sha = canonical_sha256(
            {
                "revision_id": revision_id,
                "execution_key": execution_key,
                "cells": execution_facts,
                "started_at": papermill.get("start_time"),
                "completed_at": papermill.get("end_time"),
            }
        )
        execution_id = _id("nbexec", revision_id, execution_key, execution_content_sha)
        execution_locator = f"{revision_locator}/execution/{execution_id}"
        execution_projections = project_notebook_execution_state_v2(tuple(execution_inputs))

        cell_executions: list[NotebookCellExecution] = []
        artifacts: list[NotebookArtifact] = []
        parameters: list[NotebookParameter] = []
        has_error = False
        for raw_cell, cell_version, projection, parsed_outputs in zip(
            cells_raw,
            cell_versions,
            execution_projections,
            parsed_outputs_by_cell,
            strict=True,
        ):
            valid_count = projection.execution_count
            diagnostics = set(projection.diagnostics)
            output_digests = [item.content_sha256 for item in parsed_outputs]
            cell_execution_id = _id(
                "nbcellx",
                execution_id,
                cell_version.cell_version_id,
                str(valid_count),
                canonical_sha256(output_digests),
            )
            output_ids: list[str] = []
            for ordinal, parsed_output in enumerate(parsed_outputs):
                diagnostics.update(parsed_output.diagnostics)
                content_sha = parsed_output.content_sha256
                artifact_id = _id(
                    "nbart",
                    cell_execution_id,
                    str(ordinal),
                    content_sha,
                )
                output_ids.append(artifact_id)
                if parsed_output.artifact_type == NotebookArtifactType.ERROR:
                    has_error = True
                artifacts.append(
                    NotebookArtifact(
                        artifact_id=artifact_id,
                        execution_id=execution_id,
                        cell_execution_id=cell_execution_id,
                        scope=scope,
                        ordinal=ordinal,
                        artifact_type=parsed_output.artifact_type,
                        mime_types=parsed_output.mime_types,
                        text=parsed_output.text,
                        error_name=parsed_output.error_name,
                        error_value=parsed_output.error_value,
                        binary_omitted=parsed_output.binary_omitted,
                        metric_confirmed=False,
                        content_sha256=content_sha,
                        locator=f"{execution_locator}/cell/{cell_version.stable_cell_id}/output/{ordinal}",
                    )
                )
            if (
                "parameters" in cell_version.tags
                and cell_version.cell_type == NotebookCellType.CODE
            ):
                parameter_result = parse_notebook_parameters_v2(_text(raw_cell.get("source")))
                diagnostics.update(parameter_result.diagnostics)
                for parsed_parameter in parameter_result.parameters:
                    name = _safe_identifier(parsed_parameter.name, fallback="parameter")
                    safe_value, _ = _sanitize_derived_text(
                        parsed_parameter.canonical_value,
                        limit=20_000,
                    )
                    value_sha = parsed_parameter.value_sha256
                    parameter_id = _id("nbparam", execution_id, name, value_sha)
                    parameters.append(
                        NotebookParameter(
                            parameter_id=parameter_id,
                            execution_id=execution_id,
                            cell_version_id=cell_version.cell_version_id,
                            scope=scope,
                            name=name,
                            value_type=parsed_parameter.value_type,
                            canonical_value=safe_value,
                            value_sha256=value_sha,
                            locator=f"{execution_locator}/parameter/{name}",
                        )
                    )
            cell_executions.append(
                NotebookCellExecution(
                    cell_execution_id=cell_execution_id,
                    execution_id=execution_id,
                    cell_version_id=cell_version.cell_version_id,
                    scope=scope,
                    display_order=cell_version.display_order,
                    execution_count=valid_count,
                    execution_order=projection.execution_order,
                    state=projection.state,
                    stale=projection.stale,
                    output_ids=tuple(output_ids),
                    diagnostics=tuple(sorted(diagnostics)),
                    locator=f"{execution_locator}/cell/{cell_version.stable_cell_id}",
                )
            )
        parameter_names = [item.name for item in parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise NotebookAdapterError("parameter names must be unique within one execution")

        execution = NotebookExecution(
            execution_id=execution_id,
            template_id=template_id,
            revision_id=revision_id,
            scope=scope,
            execution_key=execution_key,
            status=(
                NotebookExecutionStatus.FAILED if has_error else NotebookExecutionStatus.COMPLETED
            ),
            started_at=_timestamp(papermill.get("start_time")),
            completed_at=_timestamp(papermill.get("end_time")),
            content_sha256=execution_content_sha,
            cell_execution_ids=tuple(item.cell_execution_id for item in cell_executions),
            parameter_ids=tuple(item.parameter_id for item in parameters),
            locator=execution_locator,
        )
        analyses = tuple(
            analyze_notebook_code_v2(
                _text(raw_cell.get("source"))
                if cell_version.cell_type == NotebookCellType.CODE
                else "",
                language=language,
            )
            for raw_cell, cell_version in zip(cells_raw, cell_versions, strict=True)
        )
        symbols, dependency_edges = build_notebook_dependency_graph_v2(
            revision_id=revision_id,
            execution_id=execution_id,
            scope=scope,
            cells=tuple(cell_versions),
            executions=tuple(cell_executions),
            analyses=analyses,
        )
        structural_edges = build_notebook_structural_edges_v2(
            revision=revision,
            execution=execution,
            scope=scope,
            cells=tuple(cell_versions),
            cell_executions=tuple(cell_executions),
            parameters=tuple(parameters),
            artifacts=tuple(artifacts),
        )
        edge_by_id = {item.edge_id: item for item in (*dependency_edges, *structural_edges)}
        edges = tuple(edge_by_id[edge_id] for edge_id in sorted(edge_by_id))
        retrieval_units = build_notebook_retrieval_units_v2(
            revision=revision,
            execution=execution,
            scope=scope,
            cells=tuple(cell_versions),
            cell_executions=tuple(cell_executions),
            parameters=tuple(parameters),
            artifacts=tuple(artifacts),
        )
        values = {
            "contract_version": NOTEBOOK_CONTRACT_VERSION,
            "adapter_version": NOTEBOOK_ADAPTER_VERSION,
            "schema_version": NOTEBOOK_SCHEMA_VERSION,
            "source_payload_sha256": _sha256_bytes(payload),
            "template": template,
            "revision": revision,
            "execution": execution,
            "cell_versions": tuple(cell_versions),
            "cell_executions": tuple(cell_executions),
            "parameters": tuple(parameters),
            "symbols": symbols,
            "artifacts": tuple(artifacts),
            "retrieval_units": retrieval_units,
            "edges": edges,
            "comparisons": (),
        }
        draft = NotebookPublication.model_construct(
            publication_id="nbpub-" + ("0" * 64),
            **values,
        )
        publication_id = "nbpub-" + canonical_sha256(draft.identity_payload()).removeprefix(
            "sha256:"
        )
        return NotebookPublication(publication_id=publication_id, **values)


__all__ = [
    "MAX_CELLS",
    "MAX_NOTEBOOK_BYTES",
    "MAX_OUTPUTS_PER_CELL",
    "NotebookAdapterError",
    "NotebookAdapterV2",
]
