"""Deterministic, persistence-free AST retrieval unit construction.

The builder consumes the serializable parser IR from :mod:`evidence_rag.models`.
It deliberately returns frozen value objects instead of calling the Code V2
store.  ``CodeUnitRecord.as_store_record`` is the narrow adapter to the current
mapping-based persistence contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from ....models import ParsedCodeUnitIR, ParsedFile

BUILDER_VERSION = "c2-code-retrieval-unit-builder-v1"

UNIT_TYPE_BY_ROLE = {
    "file": "file.surface",
    "definition": "symbol.ast_block",
    "declaration": "declaration.ast_block",
    "compound": "compound.ast_block",
    "branch": "branch.ast_block",
    "loop": "loop.ast_block",
    "try": "try.ast_block",
    "fallback": "fallback.line_block",
}

_TRAILING_ORDINAL = re.compile(r"\[(\d+)\]$")


def _normalized_text(value: str, field: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{field} must be a string")
    normalized = unicodedata.normalize("NFC", value)
    if not allow_empty and not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _normalized_path(value: str) -> str:
    path = _normalized_text(value, "file_path").replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    if not path or path == ".":
        raise ValueError("file_path must identify a file")
    return path


def _normalized_pairs(
    value: Mapping[str, str] | Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    items = value.items() if isinstance(value, Mapping) else value
    normalized: dict[str, str] = {}
    for raw_key, raw_value in items:
        key = _normalized_text(raw_key, "lineage attribute key")
        item = _normalized_text(raw_value, f"lineage attribute {key}", allow_empty=True)
        if key in normalized:
            raise ValueError(f"duplicate lineage attribute: {key}")
        normalized[key] = item
    return tuple(sorted(normalized.items()))


def _normalized_strings(values: Iterable[str], field: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _normalized_text(value, field)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_digest(value: Mapping[str, str]) -> str:
    payload = json.dumps(
        {
            _normalized_text(key, "identity key"): _normalized_text(
                item, f"identity field {key}", allow_empty=True
            )
            for key, item in value.items()
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _default_source_uri(repository_id: str, ref: str, file_path: str) -> str:
    return f"code://{repository_id}@{ref}/{file_path}"


@dataclass(frozen=True, slots=True)
class CodeUnitSpan:
    """Exact UTF-8 byte and one-based line locator copied from parser IR."""

    start_line: int
    end_line: int
    start_byte: int
    end_byte: int

    def __post_init__(self) -> None:
        for field, value, minimum in (
            ("start_line", self.start_line, 1),
            ("end_line", self.end_line, 1),
            ("start_byte", self.start_byte, 0),
            ("end_byte", self.end_byte, 0),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{field} must be an integer >= {minimum}")
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        if self.end_byte < self.start_byte:
            raise ValueError("end_byte must be >= start_byte")

    def as_mapping(self) -> dict[str, int]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
        }


@dataclass(frozen=True, slots=True)
class CodeSourceLineage:
    """File-level source lineage retained on every derived retrieval unit."""

    source_uri: str
    ref: str
    file_content_hash: str = ""
    blob_hash: str = ""
    parser_version: str = ""
    attributes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_uri",
            _normalized_text(self.source_uri, "source_uri"),
        )
        object.__setattr__(self, "ref", _normalized_text(self.ref, "lineage ref"))
        object.__setattr__(
            self,
            "file_content_hash",
            _normalized_text(
                self.file_content_hash,
                "file_content_hash",
                allow_empty=True,
            ),
        )
        object.__setattr__(
            self,
            "blob_hash",
            _normalized_text(self.blob_hash, "blob_hash", allow_empty=True),
        )
        object.__setattr__(
            self,
            "parser_version",
            _normalized_text(
                self.parser_version,
                "parser_version",
                allow_empty=True,
            ),
        )
        object.__setattr__(self, "attributes", _normalized_pairs(self.attributes))

    def as_mapping(self) -> dict[str, Any]:
        return {
            "source_uri": self.source_uri,
            "ref": self.ref,
            "file_content_hash": self.file_content_hash,
            "blob_hash": self.blob_hash,
            "parser_version": self.parser_version,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class CodeUnitBuildContext:
    """Stable repository, version, access, and entity scope for parser IR."""

    project_id: str
    repository_id: str
    generation_id: str
    entity_id: str
    ref: str
    language: str
    file_path: str
    acl_ref: str
    qualified_name: str = ""
    imports: tuple[str, ...] = ()
    lineage: CodeSourceLineage | None = None

    def __post_init__(self) -> None:
        for field in (
            "project_id",
            "repository_id",
            "generation_id",
            "entity_id",
            "ref",
            "language",
            "acl_ref",
        ):
            object.__setattr__(self, field, _normalized_text(getattr(self, field), field))
        object.__setattr__(self, "file_path", _normalized_path(self.file_path))
        object.__setattr__(
            self,
            "qualified_name",
            _normalized_text(
                self.qualified_name,
                "qualified_name",
                allow_empty=True,
            ),
        )
        object.__setattr__(self, "imports", _normalized_strings(self.imports, "import"))
        if self.lineage is None:
            object.__setattr__(
                self,
                "lineage",
                CodeSourceLineage(
                    source_uri=_default_source_uri(
                        self.repository_id,
                        self.ref,
                        self.file_path,
                    ),
                    ref=self.ref,
                ),
            )
        elif not isinstance(self.lineage, CodeSourceLineage):
            raise TypeError("lineage must be CodeSourceLineage")
        elif self.lineage.ref != self.ref:
            raise ValueError("lineage ref must match build context ref")


@dataclass(frozen=True, slots=True)
class CodeUnitBuildRequest:
    """Frozen input boundary containing parser IR and its source scope."""

    context: CodeUnitBuildContext
    units: tuple[ParsedCodeUnitIR, ...]
    parse_error: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.context, CodeUnitBuildContext):
            raise TypeError("context must be CodeUnitBuildContext")
        object.__setattr__(self, "units", tuple(self.units))
        if not all(isinstance(unit, ParsedCodeUnitIR) for unit in self.units):
            raise TypeError("units must contain ParsedCodeUnitIR values")
        if self.parse_error is not None:
            object.__setattr__(
                self,
                "parse_error",
                _normalized_text(self.parse_error, "parse_error"),
            )

    @classmethod
    def from_parsed_file(
        cls,
        parsed: ParsedFile,
        *,
        project_id: str,
        repository_id: str,
        generation_id: str,
        entity_id: str,
        ref: str,
        acl_ref: str,
        qualified_name: str = "",
        source_uri: str | None = None,
        lineage_attributes: Mapping[str, str] | Iterable[tuple[str, str]] = (),
    ) -> CodeUnitBuildRequest:
        if not isinstance(parsed, ParsedFile):
            raise TypeError("parsed must be ParsedFile")
        parser_versions = {unit.parser_version for unit in parsed.units if unit.parser_version}
        parser_version = next(iter(parser_versions)) if len(parser_versions) == 1 else ""
        path = _normalized_path(parsed.path)
        lineage = CodeSourceLineage(
            source_uri=source_uri or _default_source_uri(repository_id, ref, path),
            ref=ref,
            file_content_hash=parsed.content_hash,
            blob_hash=parsed.blob_hash,
            parser_version=parser_version,
            attributes=_normalized_pairs(lineage_attributes),
        )
        return cls(
            context=CodeUnitBuildContext(
                project_id=project_id,
                repository_id=repository_id,
                generation_id=generation_id,
                entity_id=entity_id,
                ref=ref,
                language=parsed.language,
                file_path=path,
                acl_ref=acl_ref,
                qualified_name=qualified_name,
                imports=tuple(parsed.imports),
                lineage=lineage,
            ),
            units=tuple(parsed.units),
            parse_error=parsed.parse_error,
        )


@dataclass(frozen=True, slots=True)
class CodeUnitContextRef:
    """Late-context pointers; source text is not duplicated here."""

    parent_entity_id: str
    structural_path: str
    parent_structural_path: str | None
    span: CodeUnitSpan
    required_imports: tuple[str, ...]
    direct_relation_ids: tuple[str, ...]
    neighbor_unit_ids: tuple[str, ...]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "parent_entity_id": self.parent_entity_id,
            "structural_path": self.structural_path,
            "parent_structural_path": self.parent_structural_path,
            "source_range": self.span.as_mapping(),
            "required_imports": list(self.required_imports),
            "required_type_ids": [],
            "direct_relation_ids": list(self.direct_relation_ids),
            "neighbor_unit_ids": list(self.neighbor_unit_ids),
        }


@dataclass(frozen=True, slots=True)
class CodeUnitRecord:
    """One rebuildable Code V2 retrieval unit."""

    unit_id: str
    entity_id: str
    parent_unit_id: str | None
    repository_id: str
    generation_id: str
    project_id: str
    ref: str
    unit_type: str
    ast_node_type: str
    ordinal: int
    language: str
    file_path: str
    qualified_name: str
    structural_path: str
    signature: str | None
    identifiers: tuple[str, ...]
    docstring: str | None
    body: str
    content: str
    span: CodeUnitSpan
    token_count: int
    content_hash: str
    builder_version: str
    parser_version: str
    quality_status: str
    acl_ref: str
    source_lineage: CodeSourceLineage
    parse_error: str | None
    context_ref: CodeUnitContextRef

    @property
    def id(self) -> str:
        return self.unit_id

    @property
    def path(self) -> str:
        return self.file_path

    @property
    def doc(self) -> str | None:
        return self.docstring

    def as_store_record(self) -> dict[str, Any]:
        """Return a new mapping accepted by ``CodeV2StoreMixin``."""

        metadata = {
            "ref": self.ref,
            "structural_path": self.structural_path,
            "parser_version": self.parser_version,
            "parse_error": self.parse_error,
            "source_lineage": self.source_lineage.as_mapping(),
        }
        return {
            "id": self.unit_id,
            "entity_id": self.entity_id,
            "parent_unit_id": self.parent_unit_id,
            "repository_id": self.repository_id,
            "generation_id": self.generation_id,
            "project_id": self.project_id,
            "unit_type": self.unit_type,
            "ast_node_type": self.ast_node_type,
            "ordinal": self.ordinal,
            "language": self.language,
            "path": self.file_path,
            "qualified_name": self.qualified_name,
            "signature": self.signature or "",
            "identifiers": list(self.identifiers),
            "doc": self.docstring or "",
            "body": self.body,
            "content": self.content,
            "context_ref": self.context_ref.as_mapping(),
            **self.span.as_mapping(),
            "token_count": self.token_count,
            "content_hash": self.content_hash,
            "builder_version": self.builder_version,
            "quality_status": self.quality_status,
            "acl_ref": self.acl_ref,
            "metadata": metadata,
        }


@dataclass(frozen=True, slots=True)
class CodeUnitRelation:
    """Minimal deterministic entity/unit containment relation."""

    relation_id: str
    relation_type: str
    source_id: str
    source_kind: str
    target_unit_id: str
    ordinal: int
    project_id: str
    repository_id: str
    generation_id: str
    ref: str
    acl_ref: str

    def as_mapping(self) -> dict[str, Any]:
        return {
            "id": self.relation_id,
            "relation_type": self.relation_type,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "target_unit_id": self.target_unit_id,
            "ordinal": self.ordinal,
            "project_id": self.project_id,
            "repository_id": self.repository_id,
            "generation_id": self.generation_id,
            "ref": self.ref,
            "acl_ref": self.acl_ref,
        }


@dataclass(frozen=True, slots=True)
class CodeUnitBuildResult:
    """Pure builder output; callers decide whether and where to persist it."""

    records: tuple[CodeUnitRecord, ...]
    relations: tuple[CodeUnitRelation, ...]
    builder_version: str

    @property
    def units(self) -> tuple[CodeUnitRecord, ...]:
        return self.records

    def store_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(record.as_store_record() for record in self.records)


@dataclass(frozen=True, slots=True)
class _ProvisionalUnit:
    ir: ParsedCodeUnitIR
    unit_id: str
    parent_unit_id: str | None
    unit_type: str
    ordinal: int
    signature: str | None
    identifiers: tuple[str, ...]
    docstring: str | None
    content: str
    content_hash: str
    span: CodeUnitSpan


class CodeUnitBuilder:
    """Map every parser IR value to one deterministic Code V2 unit record."""

    def __init__(self, builder_version: str = BUILDER_VERSION) -> None:
        self.builder_version = _normalized_text(builder_version, "builder_version")

    def build(
        self,
        source: CodeUnitBuildRequest | ParsedFile | Iterable[ParsedCodeUnitIR],
        *,
        context: CodeUnitBuildContext | None = None,
    ) -> CodeUnitBuildResult:
        request = self._request(source, context=context)
        units = request.units
        structural_paths = [
            _normalized_text(unit.structural_path, "structural_path") for unit in units
        ]
        if len(structural_paths) != len(set(structural_paths)):
            raise ValueError("structural paths must be unique within a build request")

        first_pass: list[
            tuple[
                ParsedCodeUnitIR,
                str,
                int,
                str | None,
                tuple[str, ...],
                str | None,
                str,
                str,
                CodeUnitSpan,
            ]
        ] = []
        for unit in units:
            unit_type = UNIT_TYPE_BY_ROLE.get(unit.role, f"{unit.role}.ast_block")
            signature = (
                _normalized_text(unit.signature, "signature")
                if unit.signature is not None
                else None
            )
            docstring = _normalized_text(unit.doc, "docstring") if unit.doc is not None else None
            identifiers = _normalized_strings(unit.identifiers, "identifier")
            structural_path = _normalized_text(unit.structural_path, "structural_path")
            content = self._retrieval_text(
                request.context,
                unit,
                unit_type=unit_type,
                structural_path=structural_path,
                signature=signature,
                docstring=docstring,
                identifiers=identifiers,
            )
            content_hash = _sha256_text(content)
            first_pass.append(
                (
                    unit,
                    unit_type,
                    self._ordinal(structural_path),
                    signature,
                    identifiers,
                    docstring,
                    content,
                    content_hash,
                    CodeUnitSpan(
                        start_line=unit.start_line,
                        end_line=unit.end_line,
                        start_byte=unit.start_byte,
                        end_byte=unit.end_byte,
                    ),
                )
            )

        unit_ids_by_path = {
            structural_path: self.unit_id(
                entity_id=request.context.entity_id,
                unit_type=first_pass[index][1],
                structural_path=structural_path,
                content_hash=first_pass[index][7],
            )
            for index, structural_path in enumerate(structural_paths)
        }
        provisional: list[_ProvisionalUnit] = []
        for index, values in enumerate(first_pass):
            (
                ir,
                unit_type,
                ordinal,
                signature,
                identifiers,
                docstring,
                content,
                content_hash,
                span,
            ) = values
            parent_path = (
                _normalized_text(
                    ir.parent_structural_path,
                    "parent_structural_path",
                )
                if ir.parent_structural_path is not None
                else None
            )
            provisional.append(
                _ProvisionalUnit(
                    ir=ir,
                    unit_id=unit_ids_by_path[structural_paths[index]],
                    parent_unit_id=unit_ids_by_path.get(parent_path),
                    unit_type=unit_type,
                    ordinal=ordinal,
                    signature=signature,
                    identifiers=identifiers,
                    docstring=docstring,
                    content=content,
                    content_hash=content_hash,
                    span=span,
                )
            )

        relations = tuple(self._relation(request.context, unit) for unit in provisional)
        relation_ids = {relation.target_unit_id: relation.relation_id for relation in relations}
        neighbor_ids = self._neighbor_ids(provisional)
        lineage = request.context.lineage
        assert lineage is not None
        records = tuple(
            CodeUnitRecord(
                unit_id=unit.unit_id,
                entity_id=request.context.entity_id,
                parent_unit_id=unit.parent_unit_id,
                repository_id=request.context.repository_id,
                generation_id=request.context.generation_id,
                project_id=request.context.project_id,
                ref=request.context.ref,
                unit_type=unit.unit_type,
                ast_node_type=_normalized_text(unit.ir.node_type, "node_type"),
                ordinal=unit.ordinal,
                language=request.context.language,
                file_path=request.context.file_path,
                qualified_name=request.context.qualified_name,
                structural_path=_normalized_text(
                    unit.ir.structural_path,
                    "structural_path",
                ),
                signature=unit.signature,
                identifiers=unit.identifiers,
                docstring=unit.docstring,
                body=unit.ir.text,
                content=unit.content,
                span=unit.span,
                token_count=(len(unit.content.encode("utf-8")) + 3) // 4,
                content_hash=unit.content_hash,
                builder_version=self.builder_version,
                parser_version=_normalized_text(
                    unit.ir.parser_version,
                    "parser_version",
                    allow_empty=True,
                ),
                quality_status=_normalized_text(unit.ir.quality, "quality"),
                acl_ref=request.context.acl_ref,
                source_lineage=lineage,
                parse_error=request.parse_error,
                context_ref=CodeUnitContextRef(
                    parent_entity_id=request.context.entity_id,
                    structural_path=_normalized_text(
                        unit.ir.structural_path,
                        "structural_path",
                    ),
                    parent_structural_path=unit.ir.parent_structural_path,
                    span=unit.span,
                    required_imports=request.context.imports,
                    direct_relation_ids=(relation_ids[unit.unit_id],),
                    neighbor_unit_ids=neighbor_ids[unit.unit_id],
                ),
            )
            for unit in provisional
        )
        return CodeUnitBuildResult(
            records=records,
            relations=relations,
            builder_version=self.builder_version,
        )

    def build_unit(
        self,
        unit: ParsedCodeUnitIR,
        *,
        context: CodeUnitBuildContext,
    ) -> CodeUnitRecord:
        """Convenience boundary for callers already partitioning IR by entity."""

        return self.build((unit,), context=context).records[0]

    def unit_id(
        self,
        *,
        entity_id: str,
        unit_type: str,
        structural_path: str,
        content_hash: str,
    ) -> str:
        digest = _canonical_digest(
            {
                "builder_version": self.builder_version,
                "content_hash": content_hash,
                "entity_id": entity_id,
                "structural_path": structural_path,
                "unit_type": unit_type,
            }
        )
        return f"unit://sha256/{digest}"

    def _request(
        self,
        source: CodeUnitBuildRequest | ParsedFile | Iterable[ParsedCodeUnitIR],
        *,
        context: CodeUnitBuildContext | None,
    ) -> CodeUnitBuildRequest:
        if isinstance(source, CodeUnitBuildRequest):
            if context is not None:
                raise TypeError("context must not be repeated for CodeUnitBuildRequest")
            return source
        if context is None:
            raise TypeError("context is required unless source is CodeUnitBuildRequest")
        if not isinstance(context, CodeUnitBuildContext):
            raise TypeError("context must be CodeUnitBuildContext")
        if isinstance(source, ParsedFile):
            if _normalized_path(source.path) != context.file_path:
                raise ValueError("parsed file path must match build context")
            if _normalized_text(source.language, "language") != context.language:
                raise ValueError("parsed language must match build context")
            parser_versions = {unit.parser_version for unit in source.units if unit.parser_version}
            parser_version = next(iter(parser_versions)) if len(parser_versions) == 1 else ""
            lineage = context.lineage
            assert lineage is not None
            if lineage.file_content_hash and lineage.file_content_hash != source.content_hash:
                raise ValueError("parsed content hash conflicts with source lineage")
            if lineage.blob_hash and lineage.blob_hash != source.blob_hash:
                raise ValueError("parsed blob hash conflicts with source lineage")
            if (
                lineage.parser_version
                and parser_version
                and lineage.parser_version != parser_version
            ):
                raise ValueError("parser version conflicts with source lineage")
            enriched_lineage = replace(
                lineage,
                file_content_hash=lineage.file_content_hash or source.content_hash,
                blob_hash=lineage.blob_hash or source.blob_hash,
                parser_version=lineage.parser_version or parser_version,
            )
            return CodeUnitBuildRequest(
                context=replace(
                    context,
                    imports=context.imports or tuple(source.imports),
                    lineage=enriched_lineage,
                ),
                units=tuple(source.units),
                parse_error=source.parse_error,
            )
        return CodeUnitBuildRequest(context=context, units=tuple(source))

    def _retrieval_text(
        self,
        context: CodeUnitBuildContext,
        unit: ParsedCodeUnitIR,
        *,
        unit_type: str,
        structural_path: str,
        signature: str | None,
        docstring: str | None,
        identifiers: tuple[str, ...],
    ) -> str:
        fields = [
            f"language: {context.language}",
            f"repository: {context.repository_id}",
            f"path: {context.file_path}",
            f"unit_type: {unit_type}",
            f"ast_node_type: {_normalized_text(unit.node_type, 'node_type')}",
            f"structural_path: {structural_path}",
        ]
        if context.qualified_name:
            fields.append(f"qualified_name: {context.qualified_name}")
        if unit.parent_structural_path:
            fields.append(
                "parent_structural_path: "
                + _normalized_text(
                    unit.parent_structural_path,
                    "parent_structural_path",
                )
            )
        if signature:
            fields.append(f"signature: {signature}")
        if docstring:
            fields.append(f"doc: {docstring}")
        if context.imports:
            fields.append("imports: " + " ".join(context.imports))
        if identifiers:
            fields.append("identifiers: " + " ".join(identifiers))
        fields.extend(("body:", unit.text))
        return "\n".join(fields)

    def _relation(
        self,
        context: CodeUnitBuildContext,
        unit: _ProvisionalUnit,
    ) -> CodeUnitRelation:
        if unit.parent_unit_id is None:
            relation_type = "DEFINES"
            source_id = context.entity_id
            source_kind = "entity"
        else:
            relation_type = "CONTAINS"
            source_id = unit.parent_unit_id
            source_kind = "unit"
        relation_id = "code-unit-relation://sha256/" + _canonical_digest(
            {
                "relation_type": relation_type,
                "source_id": source_id,
                "target_unit_id": unit.unit_id,
            }
        )
        return CodeUnitRelation(
            relation_id=relation_id,
            relation_type=relation_type,
            source_id=source_id,
            source_kind=source_kind,
            target_unit_id=unit.unit_id,
            ordinal=unit.ordinal,
            project_id=context.project_id,
            repository_id=context.repository_id,
            generation_id=context.generation_id,
            ref=context.ref,
            acl_ref=context.acl_ref,
        )

    @staticmethod
    def _neighbor_ids(
        units: list[_ProvisionalUnit],
    ) -> dict[str, tuple[str, ...]]:
        siblings: dict[tuple[str | None, str | None], list[_ProvisionalUnit]] = {}
        for unit in units:
            key = (unit.parent_unit_id, unit.ir.parent_structural_path)
            siblings.setdefault(key, []).append(unit)
        result: dict[str, tuple[str, ...]] = {}
        for group in siblings.values():
            ordered = sorted(
                group,
                key=lambda item: (
                    item.span.start_byte,
                    item.span.end_byte,
                    item.ordinal,
                    item.unit_id,
                ),
            )
            for index, unit in enumerate(ordered):
                neighbors = []
                if index:
                    neighbors.append(ordered[index - 1].unit_id)
                if index + 1 < len(ordered):
                    neighbors.append(ordered[index + 1].unit_id)
                result[unit.unit_id] = tuple(neighbors)
        return result

    @staticmethod
    def _ordinal(structural_path: str) -> int:
        match = _TRAILING_ORDINAL.search(structural_path)
        return int(match.group(1)) if match else 0


ASTUnitBuilder = CodeUnitBuilder


def build_code_units(
    source: CodeUnitBuildRequest | ParsedFile | Iterable[ParsedCodeUnitIR],
    *,
    context: CodeUnitBuildContext | None = None,
    builder_version: str = BUILDER_VERSION,
) -> CodeUnitBuildResult:
    """Functional entry point for deterministic unit construction."""

    return CodeUnitBuilder(builder_version).build(source, context=context)


__all__ = [
    "ASTUnitBuilder",
    "BUILDER_VERSION",
    "UNIT_TYPE_BY_ROLE",
    "CodeSourceLineage",
    "CodeUnitBuildContext",
    "CodeUnitBuildRequest",
    "CodeUnitBuildResult",
    "CodeUnitBuilder",
    "CodeUnitContextRef",
    "CodeUnitRecord",
    "CodeUnitRelation",
    "CodeUnitSpan",
    "build_code_units",
]
