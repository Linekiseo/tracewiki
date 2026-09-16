"""Auditable Code V2 unit dual-write for one legacy ingestion generation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from ....models import EntityRecord, ParsedFile, ParsedSymbol, RepositoryIngestRequest
from ....repository import RepositorySnapshot
from .unit_builder import CodeUnitBuilder, CodeUnitBuildRequest, CodeUnitRecord

_NOT_BUILT = "not-built"
_SPARSE_INDEX_VERSION = "fts5-code-v2"
_SPARSE_RETRIEVER_VERSION = "code-exact-sparse-v1"


class CodeDualWriteStore(Protocol):
    def upsert_code_units(self, records: Iterable[Mapping[str, Any]]) -> int: ...

    def upsert_code_index_publication(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]: ...

    def code_v2_integrity_stats(
        self,
        *,
        repository_id: str | None = None,
        generation_id: str | None = None,
    ) -> dict[str, Any]: ...

    def cleanup_code_generation(
        self,
        generation_id: str,
        *,
        repository_id: str | None = None,
    ) -> dict[str, int]: ...


class CodeDensePublisher(Protocol):
    def publish(
        self,
        units: Iterable[Mapping[str, Any] | Any],
        profile: Any,
        *,
        project_id: str,
        repository_id: str,
        generation_id: str,
    ) -> Any: ...


class CodeGraphPublisher(Protocol):
    def publish(
        self,
        *,
        project_id: str,
        repository_id: str,
        generation_id: str,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class CodeDualWriteResult:
    generation_id: str
    unit_count: int
    relation_count: int
    publication_hash: str
    integrity: dict[str, Any]
    publication: dict[str, Any]


class CodeDualWriteCoordinator:
    """Build and publish foundation-only Code V2 artifacts for one generation."""

    def __init__(
        self,
        store: CodeDualWriteStore,
        builder: CodeUnitBuilder | None = None,
        *,
        dense_publisher: CodeDensePublisher | None = None,
        dense_profile: Any | None = None,
        graph_publisher: CodeGraphPublisher | None = None,
    ) -> None:
        if (dense_publisher is None) != (dense_profile is None):
            raise ValueError("dense_publisher and dense_profile must be configured together")
        self.store = store
        self.builder = builder or CodeUnitBuilder()
        self.dense_publisher = dense_publisher
        self.dense_profile = dense_profile
        self.graph_publisher = graph_publisher

    def write(
        self,
        *,
        snapshot: RepositorySnapshot,
        request: RepositoryIngestRequest,
        workflow_id: str,
        generation_id: str,
        parsed_files: Iterable[ParsedFile],
        entities: Iterable[EntityRecord],
        raw_object_ids: Mapping[str, str],
        snapshot_raw_object_id: str,
    ) -> CodeDualWriteResult:
        """Consume the existing parser IR and idempotently publish its unit records."""

        return self._write(
            snapshot=snapshot,
            request=request,
            workflow_id=workflow_id,
            generation_id=generation_id,
            parsed_files=tuple(parsed_files),
            entities=tuple(entities),
            raw_object_ids=raw_object_ids,
            snapshot_raw_object_id=snapshot_raw_object_id,
        )

    def rollback(
        self,
        *,
        repository_id: str,
        generation_id: str,
    ) -> dict[str, int]:
        """Idempotently remove all Code V2 artifacts owned by one generation."""

        return self.store.cleanup_code_generation(
            generation_id,
            repository_id=repository_id,
        )

    def _write(
        self,
        *,
        snapshot: RepositorySnapshot,
        request: RepositoryIngestRequest,
        workflow_id: str,
        generation_id: str,
        parsed_files: tuple[ParsedFile, ...],
        entities: tuple[EntityRecord, ...],
        raw_object_ids: Mapping[str, str],
        snapshot_raw_object_id: str,
    ) -> CodeDualWriteResult:
        legacy_entities: dict[str, EntityRecord] = {}
        for entity in entities:
            if entity.entity_type not in {"FileVersion", "CodeSymbol"}:
                continue
            self._validate_entity_scope(
                entity,
                snapshot=snapshot,
                generation_id=generation_id,
            )
            if entity.id in legacy_entities:
                raise ValueError("Code V2 dual-write requires unique legacy entity IDs")
            legacy_entities[entity.id] = entity
        file_entities = {
            entity.path: entity
            for entity in legacy_entities.values()
            if entity.entity_type == "FileVersion" and entity.path is not None
        }
        if len(file_entities) != sum(
            entity.entity_type == "FileVersion" for entity in legacy_entities.values()
        ):
            raise ValueError("Code V2 dual-write requires unique FileVersion paths")
        symbol_entities_by_path: dict[str, list[EntityRecord]] = {}
        for entity in legacy_entities.values():
            if entity.entity_type == "CodeSymbol" and entity.path is not None:
                symbol_entities_by_path.setdefault(entity.path, []).append(entity)

        records: list[CodeUnitRecord] = []
        relation_count = 0
        for parsed in parsed_files:
            entity = file_entities.get(parsed.path)
            if entity is None:
                raise ValueError(
                    f"Code V2 dual-write is missing FileVersion entity for {parsed.path}"
                )
            raw_object_id = raw_object_ids.get(parsed.path)
            if raw_object_id is None:
                raise ValueError(f"Code V2 dual-write is missing raw lineage for {parsed.path}")
            self._validate_entity_scope(
                entity,
                snapshot=snapshot,
                generation_id=generation_id,
            )
            build_request = self.builder_request(
                parsed=parsed,
                entity=entity,
                snapshot=snapshot,
                request=request,
                workflow_id=workflow_id,
                generation_id=generation_id,
                raw_object_id=raw_object_id,
                snapshot_raw_object_id=snapshot_raw_object_id,
            )
            result = self.builder.build(build_request)
            records.extend(
                self._project_records(
                    parsed=parsed,
                    records=result.records,
                    file_entity=entity,
                    symbol_entities=tuple(symbol_entities_by_path.get(parsed.path, ())),
                )
            )
            relation_count += len(result.relations)

        self._validate_records(
            records,
            legacy_entities=legacy_entities,
            snapshot=snapshot,
            generation_id=generation_id,
        )
        store_records = []
        for record in records:
            stored = record.as_store_record()
            lineage_attributes = dict(record.source_lineage.attributes)
            stored["metadata"]["entity_projection"] = {
                "entity_id": record.entity_id,
                "entity_type": lineage_attributes["entity_projection_type"],
                "reason": lineage_attributes["entity_projection_reason"],
            }
            store_records.append(stored)
        self.store.upsert_code_units(store_records)

        expected_units = len(store_records)
        initial_integrity = self.store.code_v2_integrity_stats(
            repository_id=snapshot.id,
            generation_id=generation_id,
        )
        self._validate_integrity(initial_integrity, expected_units, publications=None)

        publication_hash = self._publication_hash(store_records)
        projection_reasons = Counter(
            dict(record.source_lineage.attributes)["entity_projection_reason"] for record in records
        )
        projection_types = Counter(
            dict(record.source_lineage.attributes)["entity_projection_type"] for record in records
        )
        foundation = {
            "builder_version": self.builder.builder_version,
            "parser_versions": sorted({record.parser_version for record in records}),
            "files": len(parsed_files),
            "units": expected_units,
            "relations_built": relation_count,
            "publication_hash": publication_hash,
            "entity_projection": {
                "code_symbol_units": projection_types["CodeSymbol"],
                "file_version_units": projection_types["FileVersion"],
                "reasons": dict(sorted(projection_reasons.items())),
            },
        }
        capabilities = {
            "ast_unit_builder": True,
            "unit_store": True,
            "fts_row_integrity": True,
            "sparse_retrieval": True,
            "dense_retrieval": False,
            "graph_retrieval": False,
        }
        publication_base = {
            "generation_id": generation_id,
            "repository_id": snapshot.id,
            "project_id": snapshot.project_id,
            "builder": self.builder.builder_version,
            "sparse": _SPARSE_INDEX_VERSION,
            "embedding": _NOT_BUILT,
            "graph": _NOT_BUILT,
        }
        sparse_publication = {
            "schema_version": "code-sparse-publication-v1",
            "retriever_version": _SPARSE_RETRIEVER_VERSION,
            "sparse_index_version": _SPARSE_INDEX_VERSION,
            "unit_count": expected_units,
            "fts_row_count": initial_integrity["fts_indexed_units"],
            "production_ingestion": True,
        }
        self.store.upsert_code_index_publication(
            {
                **publication_base,
                "status": "building",
                "validation": {
                    "foundation": foundation,
                    "capabilities": capabilities,
                    "sparse_publication": sparse_publication,
                },
            }
        )

        integrity = self.store.code_v2_integrity_stats(
            repository_id=snapshot.id,
            generation_id=generation_id,
        )
        self._validate_integrity(integrity, expected_units, publications=1)
        publication = self.store.upsert_code_index_publication(
            {
                **publication_base,
                "status": "published",
                "validation": {
                    "foundation": foundation,
                    "capabilities": capabilities,
                    "sparse_publication": sparse_publication,
                    "integrity": integrity,
                },
            }
        )
        if self.dense_publisher is not None:
            dense_result = self.dense_publisher.publish(
                store_records,
                self.dense_profile,
                project_id=snapshot.project_id,
                repository_id=snapshot.id,
                generation_id=generation_id,
            )
            dense_publication = getattr(dense_result, "publication", None)
            if not isinstance(dense_publication, Mapping):
                raise RuntimeError("Code dense publisher returned no governed publication")
            publication = dict(dense_publication)
        if self.graph_publisher is not None:
            graph_result = self.graph_publisher.publish(
                project_id=snapshot.project_id,
                repository_id=snapshot.id,
                generation_id=generation_id,
            )
            graph_publication = getattr(graph_result, "publication", None)
            if not isinstance(graph_publication, Mapping):
                raise RuntimeError("Code graph publisher returned no governed publication")
            publication = dict(graph_publication)
        return CodeDualWriteResult(
            generation_id=generation_id,
            unit_count=expected_units,
            relation_count=relation_count,
            publication_hash=publication_hash,
            integrity=integrity,
            publication=publication,
        )

    def builder_request(
        self,
        *,
        parsed: ParsedFile,
        entity: EntityRecord,
        snapshot: RepositorySnapshot,
        request: RepositoryIngestRequest,
        workflow_id: str,
        generation_id: str,
        raw_object_id: str,
        snapshot_raw_object_id: str,
    ) -> CodeUnitBuildRequest:
        """Create the builder request while retaining request/raw/snapshot lineage."""

        return CodeUnitBuildRequest.from_parsed_file(
            parsed,
            project_id=snapshot.project_id,
            repository_id=snapshot.id,
            generation_id=generation_id,
            entity_id=entity.id,
            ref=snapshot.head_commit,
            acl_ref=snapshot.acl_ref,
            qualified_name=entity.qualified_name or parsed.path,
            lineage_attributes={
                "entity_id": entity.id,
                "generation_id": generation_id,
                "legacy_entity_source_uri": entity.source_uri,
                "raw_object_id": str(raw_object_id),
                "request_branch": request.branch or "",
                "request_history_depth": str(request.history_depth),
                "request_ignore": json.dumps(
                    sorted(request.ignore),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "request_source": request.source,
                "snapshot_base_commit": snapshot.base_commit or "",
                "snapshot_branch": snapshot.default_branch,
                "snapshot_dirty": str(snapshot.dirty).lower(),
                "snapshot_head_commit": snapshot.head_commit,
                "snapshot_raw_object_id": snapshot_raw_object_id,
                "workflow_id": workflow_id,
            },
        )

    @classmethod
    def _project_records(
        cls,
        *,
        parsed: ParsedFile,
        records: tuple[CodeUnitRecord, ...],
        file_entity: EntityRecord,
        symbol_entities: tuple[EntityRecord, ...],
    ) -> tuple[CodeUnitRecord, ...]:
        if len(records) != len(parsed.units):
            raise ValueError("Code V2 builder output no longer aligns with parser units")
        resolved_symbols = tuple(
            (symbol, entity)
            for symbol in parsed.symbols
            if (
                entity := cls._resolve_legacy_symbol(
                    symbol,
                    symbol_entities=symbol_entities,
                )
            )
            is not None
        )
        projected = []
        for record, unit in zip(records, parsed.units, strict=True):
            if unit.role == "file":
                entity = file_entity
                reason = "file-root"
            elif unit.role == "fallback":
                entity = file_entity
                reason = "parser-fallback"
            else:
                match = cls._smallest_containing_symbol(
                    record,
                    resolved_symbols=resolved_symbols,
                )
                if match is None:
                    entity = file_entity
                    reason = "no-containing-symbol"
                else:
                    entity, exact_span = match
                    reason = "exact-symbol-span" if exact_span else "smallest-containing-symbol"
            projected.append(
                cls._project_record(
                    record,
                    entity=entity,
                    file_entity=file_entity,
                    reason=reason,
                )
            )
        return tuple(projected)

    @classmethod
    def _resolve_legacy_symbol(
        cls,
        symbol: ParsedSymbol,
        *,
        symbol_entities: tuple[EntityRecord, ...],
    ) -> EntityRecord | None:
        exact_span = [
            entity
            for entity in symbol_entities
            if entity.start_line == symbol.start_line and entity.end_line == symbol.end_line
        ]
        if exact_span:
            candidates = exact_span
        else:
            candidates = [
                entity
                for entity in symbol_entities
                if entity.qualified_name == symbol.qualified_name
            ]
            if not candidates:
                return None

        qualified = [
            entity for entity in candidates if entity.qualified_name == symbol.qualified_name
        ]
        if qualified:
            candidates = qualified
        elif exact_span:
            raise ValueError("legacy CodeSymbol span conflicts with parser qualified name")

        signature = cls._symbol_signature(symbol.content)
        signature_matches = [
            entity for entity in candidates if cls._symbol_signature(entity.content) == signature
        ]
        if signature_matches:
            candidates = signature_matches
        elif signature:
            raise ValueError("legacy CodeSymbol identity conflicts with parser signature")

        unique = {entity.id: entity for entity in candidates}
        if len(unique) > 1:
            raise ValueError(
                "ambiguous legacy CodeSymbol projection for "
                f"{symbol.qualified_name}@L{symbol.start_line}-L{symbol.end_line}"
            )
        return next(iter(unique.values()), None)

    @classmethod
    def _smallest_containing_symbol(
        cls,
        record: CodeUnitRecord,
        *,
        resolved_symbols: tuple[tuple[ParsedSymbol, EntityRecord], ...],
    ) -> tuple[EntityRecord, bool] | None:
        candidates = [
            (symbol, entity)
            for symbol, entity in resolved_symbols
            if symbol.start_line <= record.span.start_line
            and symbol.end_line >= record.span.end_line
        ]
        if not candidates:
            return None

        exact_span = [
            item
            for item in candidates
            if item[0].start_line == record.span.start_line
            and item[0].end_line == record.span.end_line
        ]
        if exact_span:
            candidates = exact_span
        if record.signature:
            signature_matches = [
                item
                for item in candidates
                if cls._symbol_signature(item[0].content) == record.signature
            ]
            if signature_matches:
                candidates = signature_matches

        smallest_span = min(
            (symbol.end_line - symbol.start_line, symbol.start_line) for symbol, _ in candidates
        )
        candidates = [
            item
            for item in candidates
            if (item[0].end_line - item[0].start_line, item[0].start_line) == smallest_span
        ]
        unique = {entity.id: entity for _, entity in candidates}
        if len(unique) != 1:
            raise ValueError(
                "ambiguous containing CodeSymbol projection for "
                f"{record.file_path}@L{record.span.start_line}-L{record.span.end_line}"
            )
        return next(iter(unique.values())), bool(exact_span)

    @staticmethod
    def _project_record(
        record: CodeUnitRecord,
        *,
        entity: EntityRecord,
        file_entity: EntityRecord,
        reason: str,
    ) -> CodeUnitRecord:
        attributes = dict(record.source_lineage.attributes)
        attributes.update(
            {
                "entity_id": entity.id,
                "entity_projection_reason": reason,
                "entity_projection_type": entity.entity_type,
                "source_file_entity_id": file_entity.id,
            }
        )
        return replace(
            record,
            entity_id=entity.id,
            qualified_name=entity.qualified_name or record.qualified_name,
            source_lineage=replace(
                record.source_lineage,
                attributes=tuple(attributes.items()),
            ),
            context_ref=replace(
                record.context_ref,
                parent_entity_id=entity.id,
            ),
        )

    @staticmethod
    def _symbol_signature(content: str) -> str:
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        return " ".join(first_line.split())

    @staticmethod
    def _validate_entity_scope(
        entity: EntityRecord,
        *,
        snapshot: RepositorySnapshot,
        generation_id: str,
    ) -> None:
        expected = (
            snapshot.id,
            generation_id,
            snapshot.project_id,
            snapshot.head_commit,
            snapshot.acl_ref,
        )
        actual = (
            entity.repository_id,
            entity.generation_id,
            entity.project_id,
            entity.commit_sha,
            entity.acl_ref,
        )
        if actual != expected:
            raise ValueError("legacy code entity scope does not match the ingestion snapshot")

    @staticmethod
    def _validate_records(
        records: list[CodeUnitRecord],
        *,
        legacy_entities: Mapping[str, EntityRecord],
        snapshot: RepositorySnapshot,
        generation_id: str,
    ) -> None:
        identities = [(record.unit_id, record.generation_id) for record in records]
        if len(identities) != len(set(identities)):
            raise ValueError("Code V2 unit identities are not unique within the generation")
        unit_ids = {record.unit_id for record in records}
        for record in records:
            entity = legacy_entities.get(record.entity_id)
            if entity is None or entity.entity_type not in {"FileVersion", "CodeSymbol"}:
                raise ValueError("Code V2 unit parent is not a legacy code entity")
            if (
                record.repository_id != snapshot.id
                or record.generation_id != generation_id
                or record.project_id != snapshot.project_id
                or record.ref != snapshot.head_commit
                or record.acl_ref != snapshot.acl_ref
            ):
                raise ValueError("Code V2 unit scope does not match the ingestion snapshot")
            if entity.path != record.file_path:
                raise ValueError("Code V2 unit parent entity belongs to another file")
            if record.context_ref.parent_entity_id != entity.id:
                raise ValueError("Code V2 context parent does not match the projected entity")
            lineage_attributes = dict(record.source_lineage.attributes)
            if (
                lineage_attributes.get("entity_id") != entity.id
                or lineage_attributes.get("entity_projection_type") != entity.entity_type
            ):
                raise ValueError("Code V2 entity projection lineage is inconsistent")
            if entity.entity_type == "CodeSymbol":
                source_start_line = entity.start_line or 1
                source_end_line = entity.end_line or source_start_line
                if entity.qualified_name and record.qualified_name != entity.qualified_name:
                    raise ValueError("Code V2 symbol qualified name is inconsistent")
            else:
                source_start_line = entity.start_line or 1
                source_end_line = max(
                    entity.end_line or 1,
                    entity.content.count("\n") + 1,
                )
            if record.span.start_line < source_start_line or record.span.end_line > source_end_line:
                raise ValueError("Code V2 unit locator falls outside its projected entity")
            if record.parent_unit_id is not None and record.parent_unit_id not in unit_ids:
                raise ValueError("Code V2 unit parent is absent from the generation")

    @staticmethod
    def _validate_integrity(
        integrity: Mapping[str, Any],
        expected_units: int,
        *,
        publications: int | None,
    ) -> None:
        expected = {
            "units": expected_units,
            "active_units": expected_units,
            "vectors": 0,
            "units_without_vectors": expected_units,
            "diagnostics": 0,
            "fts_indexed_units": expected_units,
        }
        mismatches = {
            key: (integrity.get(key), value)
            for key, value in expected.items()
            if integrity.get(key) != value
        }
        if publications is not None and integrity.get("publications") != publications:
            mismatches["publications"] = (integrity.get("publications"), publications)
        if integrity.get("healthy") is not True:
            mismatches["healthy"] = (integrity.get("healthy"), True)
        if mismatches:
            raise RuntimeError(f"Code V2 generation integrity validation failed: {mismatches}")

    @staticmethod
    def _publication_hash(records: list[Mapping[str, Any]]) -> str:
        payload = [
            {
                "content_hash": record["content_hash"],
                "entity_id": record["entity_id"],
                "id": record["id"],
                "parent_unit_id": record.get("parent_unit_id"),
            }
            for record in sorted(
                records,
                key=lambda item: (str(item["id"]), str(item["generation_id"])),
            )
        ]
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CodeDualWriteCoordinator",
    "CodeDualWriteResult",
    "CodeDualWriteStore",
]
