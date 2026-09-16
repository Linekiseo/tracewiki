from __future__ import annotations

import hashlib
import posixpath
import uuid
from collections import Counter, defaultdict
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from .code_history.service import CodeHistoryError, CodeHistoryService
from .embeddings import LocalHashEmbedding
from .models import (
    EdgeRecord,
    EntityRecord,
    ParsedFile,
    RepositoryIngestRequest,
    SearchViewRecord,
)
from .parser import CodeParser
from .rag.sources.code.contracts import CodeDerivation, CodeRelationType
from .rag.sources.code.dual_write import CodeDualWriteCoordinator
from .rag.sources.code.graph_v2 import CodeGraphEntityType
from .rag.sources.code.scip_v1 import (
    ScipConsumer,
    ScipIngestionResolver,
    ScipScope,
)
from .rag.sources.code.semantic_edges_v1 import (
    SemanticEdgeEndpoint,
    SemanticEdgeScope,
    TreeSitterConservativeEdge,
    treat_python_semantic_edges,
)
from .repository import RepositoryResolver, RepositorySnapshot
from .security import secret_findings
from .sources.models import SourceEventInput
from .sources.service import RawSourceService
from .storage import SQLiteStore


def stable_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def edge_id(edge_type: str, source_id: str, target_id: str) -> str:
    return f"edge://sha256:{stable_hash(edge_type, source_id, target_id)}"


class IngestionService:
    def __init__(
        self,
        store: SQLiteStore,
        resolver: RepositoryResolver,
        parser: CodeParser,
        embedder: LocalHashEmbedding,
        sources: RawSourceService,
        history: CodeHistoryService,
        code_dual_writer: CodeDualWriteCoordinator | None = None,
        code_semantic_consumer: ScipConsumer | None = None,
    ) -> None:
        self.store = store
        self.resolver = resolver
        self.parser = parser
        self.embedder = embedder
        self.sources = sources
        self.history = history
        self.code_dual_writer = code_dual_writer
        self.code_semantic_consumer = code_semantic_consumer

    def enqueue(self, request: RepositoryIngestRequest) -> str:
        workflow_id = f"wf-{uuid.uuid4()}"
        self.store.create_workflow(workflow_id, request.source, request.model_dump())
        return workflow_id

    def run(self, workflow_id: str, request: RepositoryIngestRequest) -> None:
        generation_id: str | None = None
        repository_id: str | None = None
        semantic_raw_object_id: str | None = None
        semantic_entity_ids: tuple[str, ...] = ()
        try:
            self.store.update_workflow(workflow_id, status="running", stage="discover", progress=3)
            snapshot = self.resolver.resolve(
                request.source,
                project_id=request.project_id,
                acl_ref=request.acl_ref,
                branch=request.branch,
                history_depth=request.history_depth,
            )
            repository_id = snapshot.id
            snapshot_raw = self.sources.accept(
                SourceEventInput(
                    source_type="git",
                    source_instance=snapshot.source_url or str(snapshot.local_path),
                    event_type="repository.snapshot.observed",
                    source_object_id=snapshot.id,
                    source_version=snapshot.head_commit,
                    event_time=None,
                    project_id=request.project_id,
                    acl_ref=request.acl_ref,
                    source_uri=snapshot.source_url or str(snapshot.local_path),
                    payload={
                        "repository_id": snapshot.id,
                        "head_commit": snapshot.head_commit,
                        "base_commit": snapshot.base_commit,
                        "branch": snapshot.default_branch,
                        "dirty": snapshot.dirty,
                    },
                    adapter_version="git-resolver-v2",
                    metadata={"workflow_id": workflow_id},
                )
            )["raw_object"]
            generation_id = f"gen-{uuid.uuid4()}"
            self.store.prepare_generation(
                generation_id=generation_id,
                repository=snapshot.as_storage_dict(),
                parser_versions={
                    "code_parser": self.parser.parser_version,
                    "embedding": self.embedder.model_id,
                    "schema": "code-evidence-v1",
                },
            )
            self.store.update_workflow(
                workflow_id,
                repository_id=repository_id,
                generation_id=generation_id,
            )

            paths = self.resolver.discover_files(snapshot, request.ignore)
            counters: dict[str, int] = {
                "discovered": len(paths),
                "files": 0,
                "symbols": 0,
                "edges": 0,
                "views": 0,
                "parse_errors": 0,
                "skipped": 0,
                "quarantined": 0,
                "unresolved_calls": 0,
                "unresolved_references": 0,
                "unresolved_imports": 0,
                "raw_objects": 1,
            }
            self.store.update_workflow(workflow_id, stage="capture", progress=12, counters=counters)

            parsed_files: list[ParsedFile] = []
            raw_objects: dict[str, str] = {}
            for index, path in enumerate(paths):
                content = self.resolver.read_text(path)
                if content is None:
                    counters["skipped"] += 1
                    continue
                findings = secret_findings(content)
                relative = path.relative_to(snapshot.local_path).as_posix()
                if findings:
                    counters["quarantined"] += 1
                    quarantined = self.sources.persist_bytes(
                        project_id=request.project_id,
                        source_type="git",
                        source_instance=snapshot.id,
                        source_object_id=relative,
                        source_version=snapshot.head_commit,
                        payload=None,
                        source_uri=f"{snapshot.id}@{snapshot.head_commit}:{relative}",
                        media_type="text/plain",
                        acl_ref=request.acl_ref,
                        adapter_version="git-file-v1",
                        schema_version="raw-code-v1",
                        state="quarantined",
                        metadata={"secret_findings": findings, "workflow_id": workflow_id},
                        content_hash="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
                    )
                    raw_objects[relative] = quarantined["id"]
                    counters["raw_objects"] += 1
                    continue
                blob = self.resolver.blob_hash(snapshot, path, content)
                parsed = self.parser.parse(relative, content, blob)
                raw = self.sources.persist_bytes(
                    project_id=request.project_id,
                    source_type="git",
                    source_instance=snapshot.id,
                    source_object_id=relative,
                    source_version=snapshot.head_commit,
                    payload=content.encode("utf-8"),
                    source_uri=f"{snapshot.id}@{snapshot.head_commit}:{relative}",
                    media_type="text/plain; charset=utf-8",
                    acl_ref=request.acl_ref,
                    adapter_version="git-file-v1",
                    schema_version="raw-code-v1",
                    metadata={"blob_hash": blob, "workflow_id": workflow_id},
                    content_hash=parsed.content_hash,
                )
                raw_objects[relative] = raw["id"]
                counters["raw_objects"] += 1
                if parsed.parse_error:
                    counters["parse_errors"] += 1
                parsed_files.append(parsed)
                if index % 25 == 0:
                    progress = 15 + int(30 * (index + 1) / max(1, len(paths)))
                    self.store.update_workflow(
                        workflow_id, stage="parse", progress=progress, counters=counters
                    )

            self.store.update_workflow(
                workflow_id, stage="structure", progress=50, counters=counters
            )
            entities, edges, views, validation = self._build_records(
                snapshot, generation_id, parsed_files
            )
            if self.code_semantic_consumer is not None:
                (
                    edges,
                    semantic_validation,
                    semantic_raw_object_id,
                    semantic_entity_ids,
                ) = self._resolve_semantic_edges(
                    snapshot=snapshot,
                    request=request,
                    generation_id=generation_id,
                    workflow_id=workflow_id,
                    entities=entities,
                    edges=edges,
                )
                validation["semantic_resolver"] = semantic_validation
                counters["semantic_edges"] = int(
                    semantic_validation["materialized_scip_edge_count"]
                )
                counters["semantic_diagnostics"] = int(semantic_validation["diagnostic_count"])
            counters.update(
                files=len(parsed_files),
                symbols=sum(len(item.symbols) for item in parsed_files),
                edges=len(edges),
                views=len(views),
                unresolved_calls=validation["unresolved_calls"],
                unresolved_references=validation["unresolved_references"],
                unresolved_imports=validation["unresolved_imports"],
            )
            try:
                history_result = self.history.sync(
                    snapshot.id,
                    depth=request.history_depth,
                    include_diffs=True,
                    fetch_remote=False,
                )
            except CodeHistoryError as exc:
                history_result = {
                    "repository_id": snapshot.id,
                    "status": "partial",
                    "commits": 0,
                    "branches": 0,
                    "hunks": 0,
                    "error": str(exc),
                    "action": "synchronize missing Git history objects and retry",
                }
                counters["history_errors"] = 1
            counters["commits"] = int(history_result.get("commits", 0))
            counters["diff_hunks"] = int(history_result.get("hunks", 0))
            validation["history"] = history_result
            self.store.update_workflow(workflow_id, stage="index", progress=78, counters=counters)
            self.store.publish_generation(
                repository_id=snapshot.id,
                generation_id=generation_id,
                entities=entities,
                edges=edges,
                views=views,
                counts=counters,
                validation=validation,
            )
            if self.code_dual_writer is not None:
                code_v2_result = self.code_dual_writer.write(
                    snapshot=snapshot,
                    request=request,
                    workflow_id=workflow_id,
                    generation_id=generation_id,
                    parsed_files=parsed_files,
                    entities=entities,
                    raw_object_ids=raw_objects,
                    snapshot_raw_object_id=snapshot_raw["id"],
                )
                if "semantic_resolver" in validation:
                    validation["semantic_resolver"]["graph_ready_unit_count"] = (
                        self._mark_semantic_graph_units_ready(
                            repository_id=snapshot.id,
                            generation_id=generation_id,
                            entity_ids=semantic_entity_ids,
                        )
                    )
                    self._publish_semantic_validation(
                        code_v2_result.publication,
                        validation["semantic_resolver"],
                    )
            self.sources.store.link_derivations(
                snapshot_raw["id"],
                [item.id for item in entities if item.entity_type in {"Repository", "Commit"}],
                kind="repository_snapshot",
                generation_id=generation_id,
                derivation_version="code-evidence-v1",
            )
            for path, raw_object_id in raw_objects.items():
                self.sources.store.link_derivations(
                    raw_object_id,
                    [item.id for item in entities if item.path == path],
                    kind="code_parse",
                    generation_id=generation_id,
                    derivation_version=self.parser.parser_version,
                )
            if semantic_raw_object_id is not None and semantic_entity_ids:
                self.sources.store.link_derivations(
                    semantic_raw_object_id,
                    list(semantic_entity_ids),
                    kind="scip_semantic_edge",
                    generation_id=generation_id,
                    derivation_version="c5-python-semantic-edges-v1",
                )
            self.store.update_workflow(
                workflow_id,
                status="completed",
                stage="publish",
                progress=100,
                counters=counters,
            )
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if (
                self.code_dual_writer is not None
                and generation_id is not None
                and repository_id is not None
            ):
                try:
                    self.code_dual_writer.rollback(
                        repository_id=repository_id,
                        generation_id=generation_id,
                    )
                except Exception as cleanup_exc:
                    message += (
                        f"; Code V2 cleanup failed: {type(cleanup_exc).__name__}: {cleanup_exc}"
                    )
            self.store.fail_generation(generation_id, repository_id, message)
            self.store.update_workflow(
                workflow_id,
                status="failed",
                stage="failed",
                progress=100,
                error=message,
            )

    def _resolve_semantic_edges(
        self,
        *,
        snapshot: RepositorySnapshot,
        request: RepositoryIngestRequest,
        generation_id: str,
        workflow_id: str,
        entities: list[EntityRecord],
        edges: list[EdgeRecord],
    ) -> tuple[list[EdgeRecord], dict[str, Any], str | None, tuple[str, ...]]:
        """Consume a caller-supplied local index and publish only governed SCIP edges."""

        consumer = self.code_semantic_consumer
        if consumer is None:
            raise RuntimeError("semantic resolution requires an injected SCIP consumer")
        scip_scope = ScipScope(
            project_id=snapshot.project_id,
            repository_id=snapshot.id,
            generation_id=generation_id,
            acl_ref=snapshot.acl_ref,
        )
        resolver = ScipIngestionResolver(entities, scope=scip_scope)
        index_path = snapshot.local_path / "index.scip"
        raw_object_id: str | None = None
        index_status = "unavailable"
        scip_result = None
        if index_path.exists():
            root = snapshot.local_path.resolve()
            safe = (
                not index_path.is_symlink()
                and index_path.is_file()
                and index_path.resolve().parent == root
            )
            if not safe:
                index_status = "rejected_unsafe_path"
            else:
                maximum = consumer.decoder.limits.max_file_bytes
                size = index_path.stat().st_size
                if size > maximum:
                    index_status = "rejected_oversize"
                else:
                    payload = index_path.read_bytes()
                    if len(payload) != size or len(payload) > maximum:
                        raise ValueError("SCIP index changed during bounded local ingestion")
                    raw = self.sources.persist_bytes(
                        project_id=request.project_id,
                        source_type="git",
                        source_instance=snapshot.id,
                        source_object_id="index.scip",
                        source_version=snapshot.head_commit,
                        payload=payload,
                        source_uri=(f"{snapshot.id}@{snapshot.head_commit}:index.scip"),
                        media_type="application/vnd.sourcegraph.scip",
                        acl_ref=request.acl_ref,
                        adapter_version="c5-scip-consumer-v1",
                        schema_version="scip-index-v1",
                        metadata={
                            "workflow_id": workflow_id,
                            "resolver": "scip-python",
                            "execution": "provided-local-index",
                        },
                        content_hash="sha256:" + hashlib.sha256(payload).hexdigest(),
                    )
                    raw_object_id = raw["id"]
                    scip_result = consumer.consume_bytes(
                        payload,
                        repo_root=snapshot.local_path,
                        scope=scip_scope,
                        resolver=resolver,
                        raw_object_id=raw_object_id,
                        source_name="provided:index.scip",
                    )
                    index_status = scip_result.status.value

        by_entity_id = {entity.id: entity for entity in entities}
        semantic_scope = SemanticEdgeScope(
            project_id=snapshot.project_id,
            repository_id=snapshot.id,
            generation_id=generation_id,
            stable_version=snapshot.head_commit,
            acl_ref=snapshot.acl_ref,
        )
        tree_edges = []
        for edge in edges:
            if edge.edge_type not in {
                CodeRelationType.CALLS.value,
                CodeRelationType.REFERENCES.value,
                CodeRelationType.IMPLEMENTS.value,
                CodeRelationType.OVERRIDES.value,
            }:
                continue
            source = by_entity_id.get(edge.source_id)
            target = by_entity_id.get(edge.target_id)
            if source is None or target is None:
                continue
            if source.language != "python" or target.language != "python":
                continue
            try:
                tree_edges.append(
                    TreeSitterConservativeEdge(
                        edge_type=CodeRelationType(edge.edge_type),
                        source=self._semantic_endpoint(source, semantic_scope),
                        target=self._semantic_endpoint(target, semantic_scope),
                        confidence=edge.confidence,
                        evidence_locators=(edge.evidence_locator or source.source_uri,),
                        parser_version=self.parser.parser_version,
                        resolver_version="legacy-static-linker-v1",
                    )
                )
            except (TypeError, ValueError):
                continue

        treatment = treat_python_semantic_edges(
            scip_result=scip_result,
            tree_sitter_edges=tree_edges,
            scope=semantic_scope,
            language="python",
        )
        resolved = {(edge.id, edge.generation_id): edge for edge in edges}
        linked_entities: set[str] = set()
        scip_edges = 0
        for semantic_edge in treatment.edges:
            if not any(
                provenance.derivation is CodeDerivation.SCIP
                for provenance in semantic_edge.provenances
            ):
                continue
            materialized = self._edge(
                snapshot,
                generation_id,
                semantic_edge.edge_type.value,
                semantic_edge.source.entity_id,
                semantic_edge.target.entity_id,
                semantic_edge.evidence_locators[0],
                derivation=CodeDerivation.SCIP.value,
                confidence=semantic_edge.confidence,
            )
            resolved[(materialized.id, materialized.generation_id)] = materialized
            linked_entities.update((materialized.source_id, materialized.target_id))
            scip_edges += 1
        for entity in entities:
            if entity.id in linked_entities:
                entity.source_uri = self._semantic_graph_locator(snapshot, entity)

        validation = {
            "resolver": "scip-python",
            "consumer_version": getattr(consumer, "consumer_version", "c5-scip-consumer-v1"),
            "treatment_version": treatment.treatment_version,
            "index_status": index_status,
            "status": treatment.status.value,
            "support_status": treatment.support_status.value,
            "semantic_ready": treatment.semantic_ready,
            "materialized_scip_edge_count": scip_edges,
            "diagnostic_count": len(treatment.diagnostics),
            "diagnostic_codes": sorted({item.code.value for item in treatment.diagnostics}),
            "conflict_count": len(treatment.conflicts),
            "result_hash": treatment.result_hash,
            "fallback_used": treatment.trace.fallback_used,
            "fallback_reason": treatment.trace.reason,
            "external_execution": False,
            "raw_object_id": raw_object_id,
        }
        return (
            list(resolved.values()),
            validation,
            raw_object_id,
            tuple(sorted(linked_entities)),
        )

    @staticmethod
    def _semantic_endpoint(
        entity: EntityRecord,
        scope: SemanticEdgeScope,
    ) -> SemanticEdgeEndpoint:
        return SemanticEdgeEndpoint(
            entity_id=entity.id,
            entity_type=CodeGraphEntityType(entity.entity_type),
            project_id=scope.project_id,
            repository_id=scope.repository_id,
            generation_id=scope.generation_id,
            stable_version=scope.stable_version,
            acl_ref=scope.acl_ref,
            locator=entity.source_uri,
        )

    @staticmethod
    def _semantic_graph_locator(
        snapshot: RepositorySnapshot,
        entity: EntityRecord,
    ) -> str:
        if entity.path is None:
            raise ValueError("SCIP graph endpoint requires a repository path")
        start_line = entity.start_line or 1
        end_line = entity.end_line or start_line
        path = quote(entity.path, safe="/")
        commit = quote(snapshot.head_commit, safe=".+-")
        return f"code://{snapshot.id}@{commit}/{path}#L{start_line}-L{end_line}"

    def _publish_semantic_validation(
        self,
        publication: dict[str, Any],
        semantic_validation: dict[str, Any],
    ) -> None:
        validation = dict(publication.get("validation") or {})
        capabilities = dict(validation.get("capabilities") or {})
        capabilities["scip_semantic_edges"] = bool(semantic_validation.get("semantic_ready"))
        validation["capabilities"] = capabilities
        validation["semantic_resolver"] = dict(semantic_validation)
        self.store.upsert_code_index_publication(
            {
                "generation_id": publication["generation_id"],
                "repository_id": publication["repository_id"],
                "project_id": publication["project_id"],
                "builder": publication["builder"],
                "sparse": publication["sparse"],
                "embedding": publication["embedding"],
                "graph": publication["graph"],
                "status": publication["status"],
                "validation": validation,
            }
        )

    def _mark_semantic_graph_units_ready(
        self,
        *,
        repository_id: str,
        generation_id: str,
        entity_ids: tuple[str, ...],
    ) -> int:
        """Expose fully built units to the existing graph adjacency readiness gate."""

        if not entity_ids:
            return 0
        marks = ",".join("?" for _ in entity_ids)
        with self.store.transaction() as database:
            cursor = database.execute(
                f"""
                UPDATE code_retrieval_units
                SET quality_status='ready'
                WHERE repository_id=? AND generation_id=?
                  AND quality_status='complete'
                  AND entity_id IN ({marks})
                """,
                (repository_id, generation_id, *entity_ids),
            )
        return max(0, int(cursor.rowcount))

    def _build_records(
        self,
        snapshot: RepositorySnapshot,
        generation_id: str,
        parsed_files: list[ParsedFile],
    ) -> tuple[list[EntityRecord], list[EdgeRecord], list[SearchViewRecord], dict[str, Any]]:
        entities: list[EntityRecord] = []
        edges: list[EdgeRecord] = []
        views: list[SearchViewRecord] = []
        repo_identity = snapshot.id.removeprefix("repo://")
        commit_id = f"git://{repo_identity}/commit/{quote(snapshot.head_commit, safe='.+-')}"
        repository_entity = EntityRecord(
            id=snapshot.id,
            repository_id=snapshot.id,
            generation_id=generation_id,
            project_id=snapshot.project_id,
            entity_type="Repository",
            name=snapshot.name,
            qualified_name=None,
            path=None,
            language=None,
            commit_sha=snapshot.head_commit,
            blob_hash=None,
            content_hash="sha256:" + stable_hash(snapshot.id, snapshot.head_commit),
            start_line=None,
            end_line=None,
            source_uri=snapshot.source_url or snapshot.id,
            acl_ref=snapshot.acl_ref,
            content=f"Repository {snapshot.name} at {snapshot.head_commit}",
            metadata={
                "branch": snapshot.default_branch,
                "dirty": snapshot.dirty,
                "base_commit": snapshot.base_commit,
            },
        )
        commit_entity = EntityRecord(
            id=commit_id,
            repository_id=snapshot.id,
            generation_id=generation_id,
            project_id=snapshot.project_id,
            entity_type="Commit",
            name=snapshot.head_commit[:12],
            qualified_name=None,
            path=None,
            language=None,
            commit_sha=snapshot.head_commit,
            blob_hash=None,
            content_hash="sha256:" + stable_hash(commit_id),
            start_line=None,
            end_line=None,
            source_uri=commit_id,
            acl_ref=snapshot.acl_ref,
            content=f"Commit {snapshot.head_commit} on {snapshot.default_branch}",
            metadata={"dirty": snapshot.dirty, "base_commit": snapshot.base_commit},
        )
        entities.extend([repository_entity, commit_entity])
        edges.append(
            self._edge(snapshot, generation_id, "HAS_COMMIT", snapshot.id, commit_id, commit_id)
        )

        file_entities: dict[str, EntityRecord] = {}
        symbol_entries: list[tuple[EntityRecord, ParsedFile, str]] = []
        for parsed in parsed_files:
            encoded_path = quote(parsed.path, safe="/")
            file_id = (
                f"code://{repo_identity}@{quote(snapshot.head_commit, safe='.+-')}/{encoded_path}"
            )
            locator = self._locator(
                snapshot, parsed.path, 1, max(1, len(parsed.content.splitlines()))
            )
            file_entity = EntityRecord(
                id=file_id,
                repository_id=snapshot.id,
                generation_id=generation_id,
                project_id=snapshot.project_id,
                entity_type="FileVersion",
                name=PurePosixPath(parsed.path).name,
                qualified_name=None,
                path=parsed.path,
                language=parsed.language,
                commit_sha=snapshot.head_commit,
                blob_hash=parsed.blob_hash,
                content_hash=parsed.content_hash,
                start_line=1,
                end_line=max(1, len(parsed.content.splitlines())),
                source_uri=locator,
                acl_ref=snapshot.acl_ref,
                content=parsed.content,
                metadata={
                    "parse_error": parsed.parse_error,
                    "imports": parsed.imports,
                    "line_count": max(1, len(parsed.content.splitlines())),
                },
            )
            entities.append(file_entity)
            file_entities[parsed.path] = file_entity
            edges.append(
                self._edge(snapshot, generation_id, "CONTAINS", commit_id, file_id, locator)
            )
            views.append(self._view(file_entity, "file.raw", parsed.content))

            qualified_name_counts = Counter(item.qualified_name for item in parsed.symbols)
            duplicate_signature_counts: dict[tuple[str, str], int] = defaultdict(int)
            for symbol in parsed.symbols:
                symbol_key = symbol.qualified_name
                if qualified_name_counts[symbol.qualified_name] > 1:
                    signature = self._symbol_signature(symbol.content)
                    duplicate_key = (symbol.qualified_name, signature)
                    duplicate_signature_counts[duplicate_key] += 1
                    discriminator = stable_hash(symbol.kind, signature)[:12]
                    symbol_key = f"{symbol.qualified_name}~{discriminator}"
                    if duplicate_signature_counts[duplicate_key] > 1:
                        symbol_key += f"-{duplicate_signature_counts[duplicate_key]}"
                symbol_id = f"{file_id}#symbol={quote(symbol_key, safe='._$~-')}"
                symbol_locator = self._locator(
                    snapshot, parsed.path, symbol.start_line, symbol.end_line
                )
                entity = EntityRecord(
                    id=symbol_id,
                    repository_id=snapshot.id,
                    generation_id=generation_id,
                    project_id=snapshot.project_id,
                    entity_type="CodeSymbol",
                    name=symbol.name,
                    qualified_name=symbol.qualified_name,
                    path=parsed.path,
                    language=parsed.language,
                    commit_sha=snapshot.head_commit,
                    blob_hash=parsed.blob_hash,
                    content_hash="sha256:" + hashlib.sha256(symbol.content.encode()).hexdigest(),
                    start_line=symbol.start_line,
                    end_line=symbol.end_line,
                    source_uri=symbol_locator,
                    acl_ref=snapshot.acl_ref,
                    content=symbol.content,
                    metadata={
                        "kind": symbol.kind,
                        "calls": symbol.calls,
                        "references": symbol.references,
                    },
                )
                entities.append(entity)
                symbol_entries.append((entity, parsed, file_id))
                edges.append(
                    self._edge(
                        snapshot, generation_id, "DEFINES", file_id, symbol_id, symbol_locator
                    )
                )
                view_content = (
                    f"{symbol.kind} {symbol.qualified_name}\n"
                    f"path: {parsed.path}\nlanguage: {parsed.language}\n\n{symbol.content}"
                )
                views.append(self._view(entity, "symbol.raw", view_content))

        unresolved_imports = self._link_imports(
            snapshot, generation_id, parsed_files, file_entities, edges
        )
        unresolved_calls = self._link_calls(snapshot, generation_id, symbol_entries, edges)
        unresolved_references = self._link_references(
            snapshot, generation_id, symbol_entries, edges
        )
        entity_ids = [item.id for item in entities]
        validation = {
            "entity_ids_unique": len(entity_ids) == len(set(entity_ids)),
            "edge_ids_unique": len({item.id for item in edges}) == len(edges),
            "unresolved_calls": unresolved_calls,
            "unresolved_references": unresolved_references,
            "unresolved_imports": unresolved_imports,
            "parse_errors": sum(bool(item.parse_error) for item in parsed_files),
        }
        if not validation["entity_ids_unique"]:
            duplicate_ids = [
                entity_id for entity_id, count in Counter(entity_ids).items() if count > 1
            ]
            raise ValueError(
                "duplicate stable entity IDs detected: " + ", ".join(duplicate_ids[:5])
            )
        # Duplicate call/import edges are harmless semantically but violate the storage key.
        unique_edges = {(item.id, item.generation_id): item for item in edges}
        return entities, list(unique_edges.values()), views, validation

    @staticmethod
    def _symbol_signature(content: str) -> str:
        """Return a body-independent signature used only to disambiguate overloads."""
        first_line = next((line.strip() for line in content.splitlines() if line.strip()), "")
        return " ".join(first_line.split())

    def _link_calls(
        self,
        snapshot: RepositorySnapshot,
        generation_id: str,
        entries: list[tuple[EntityRecord, ParsedFile, str]],
        edges: list[EdgeRecord],
    ) -> int:
        by_name: dict[str, list[EntityRecord]] = defaultdict(list)
        symbol_file_ids: dict[str, str] = {}
        for entity, _, file_id in entries:
            by_name[entity.name].append(entity)
            symbol_file_ids[entity.id] = file_id
        imported_file_ids: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            if edge.edge_type == "IMPORTS":
                imported_file_ids[edge.source_id].add(edge.target_id)
        unresolved = 0
        for source, _parsed, source_file_id in entries:
            calls = source.metadata.get("calls", [])
            for called_name in calls:
                matches = by_name.get(called_name, [])
                local = [item for item in matches if item.path == source.path]
                imported = [
                    item
                    for item in matches
                    if symbol_file_ids.get(item.id) in imported_file_ids.get(source_file_id, set())
                ]
                target = (
                    local[0] if len(local) == 1 else imported[0] if len(imported) == 1 else None
                )
                if not target or target.id == source.id:
                    unresolved += 1
                    continue
                confidence = 0.9 if target.path == source.path else 0.82
                edges.append(
                    self._edge(
                        snapshot,
                        generation_id,
                        "CALLS",
                        source.id,
                        target.id,
                        source.source_uri,
                        derivation="static_analysis",
                        confidence=confidence,
                    )
                )
        return unresolved

    def _link_references(
        self,
        snapshot: RepositorySnapshot,
        generation_id: str,
        entries: list[tuple[EntityRecord, ParsedFile, str]],
        edges: list[EdgeRecord],
    ) -> int:
        """Link non-call identifier use without turning every local name into a node.

        A reference is published only when it resolves to exactly one symbol in the
        current file or exactly one symbol in a statically imported file.  CALLS wins
        when both analyses identify the same pair, keeping the graph meaningful.
        """

        by_name: dict[str, list[EntityRecord]] = defaultdict(list)
        symbol_file_ids: dict[str, str] = {}
        for entity, _, file_id in entries:
            by_name[entity.name].append(entity)
            symbol_file_ids[entity.id] = file_id
        imported_file_ids: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            if edge.edge_type == "IMPORTS":
                imported_file_ids[edge.source_id].add(edge.target_id)
        call_pairs = {
            (edge.source_id, edge.target_id) for edge in edges if edge.edge_type == "CALLS"
        }
        unresolved = 0
        for source, _parsed, source_file_id in entries:
            references = source.metadata.get("references", [])
            published = 0
            for referenced_name in references:
                matches = by_name.get(referenced_name, [])
                local = [item for item in matches if item.path == source.path]
                imported = [
                    item
                    for item in matches
                    if symbol_file_ids.get(item.id) in imported_file_ids.get(source_file_id, set())
                ]
                target = (
                    local[0] if len(local) == 1 else imported[0] if len(imported) == 1 else None
                )
                if not target or target.id == source.id:
                    unresolved += 1
                    continue
                if (source.id, target.id) in call_pairs:
                    continue
                edges.append(
                    self._edge(
                        snapshot,
                        generation_id,
                        "REFERENCES",
                        source.id,
                        target.id,
                        source.source_uri,
                        derivation="static_analysis",
                        confidence=0.78 if target.path == source.path else 0.72,
                    )
                )
                published += 1
                if published >= 80:
                    break
        return unresolved

    def _link_imports(
        self,
        snapshot: RepositorySnapshot,
        generation_id: str,
        parsed_files: list[ParsedFile],
        files: dict[str, EntityRecord],
        edges: list[EdgeRecord],
    ) -> int:
        keys: dict[str, list[EntityRecord]] = defaultdict(list)
        suffix_keys: dict[str, list[EntityRecord]] = defaultdict(list)
        for path, entity in files.items():
            item = PurePosixPath(path)
            without_suffix = str(item.with_suffix(""))
            variants = {
                path,
                without_suffix,
                without_suffix.replace("/", "."),
                item.stem,
                item.name,
            }
            if item.stem in {"index", "__init__"}:
                variants.add(str(item.parent))
                variants.add(str(item.parent).replace("/", "."))
            for key in variants:
                keys[key].append(entity)
            parts = PurePosixPath(without_suffix).parts
            for offset in range(len(parts)):
                suffix_keys["/".join(parts[offset:])].append(entity)

        unresolved = 0
        for parsed in parsed_files:
            source = files[parsed.path]
            for imported in parsed.imports:
                candidates = self._import_keys(parsed.path, imported)
                matches: dict[str, EntityRecord] = {}
                for key in candidates:
                    for entity in keys.get(key, []):
                        matches[entity.id] = entity
                if not matches:
                    # Package imports often include an organization prefix; suffix match is safe
                    # only when it resolves to exactly one indexed file.
                    normalized = imported.replace(".", "/").strip("/")
                    suffix_matches = suffix_keys.get(normalized, [])
                    if len(suffix_matches) == 1:
                        matches[suffix_matches[0].id] = suffix_matches[0]
                if len(matches) != 1:
                    unresolved += 1
                    continue
                target = next(iter(matches.values()))
                if target.id == source.id:
                    continue
                edges.append(
                    self._edge(
                        snapshot,
                        generation_id,
                        "IMPORTS",
                        source.id,
                        target.id,
                        source.source_uri,
                        derivation="static_analysis",
                        confidence=0.85,
                    )
                )
        return unresolved

    def _import_keys(self, source_path: str, imported: str) -> set[str]:
        clean = imported.strip().strip("'\"").removesuffix(".*")
        keys = {clean, clean.replace(".", "/")}
        if clean.startswith("."):
            parent = str(PurePosixPath(source_path).parent)
            joined = posixpath.normpath(posixpath.join(parent, clean))
            keys.update({joined, joined.replace("/", ".")})
        for extension in (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs"):
            keys.add(clean + extension)
        return {key.strip("./") for key in keys if key}

    def _edge(
        self,
        snapshot: RepositorySnapshot,
        generation_id: str,
        kind: str,
        source_id: str,
        target_id: str,
        locator: str,
        *,
        derivation: str = "deterministic",
        confidence: float = 1.0,
    ) -> EdgeRecord:
        return EdgeRecord(
            id=edge_id(kind, source_id, target_id),
            repository_id=snapshot.id,
            generation_id=generation_id,
            source_id=source_id,
            target_id=target_id,
            edge_type=kind,
            derivation=derivation,
            confidence=confidence,
            evidence_locator=locator,
        )

    def _view(self, entity: EntityRecord, view_type: str, content: str) -> SearchViewRecord:
        return SearchViewRecord(
            id=f"view://{stable_hash(entity.id, view_type)}",
            entity_id=entity.id,
            repository_id=entity.repository_id,
            generation_id=entity.generation_id,
            project_id=entity.project_id,
            view_type=view_type,
            name=entity.qualified_name or entity.name,
            path=entity.path or "",
            language=entity.language or "",
            content=content,
            vector=self.embedder.embed(content),
            embedding_model=self.embedder.model_id,
        )

    def _locator(
        self, snapshot: RepositorySnapshot, path: str, start_line: int, end_line: int
    ) -> str:
        return f"{snapshot.name}@{snapshot.head_commit}:{path}#L{start_line}-L{end_line}"
