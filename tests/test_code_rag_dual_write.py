from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.embeddings import LocalHashEmbedding
from evidence_rag.ingestion import IngestionService
from evidence_rag.models import ParsedFile, RepositoryIngestRequest
from evidence_rag.parser import CodeParser
from evidence_rag.rag.sources.code import CodeDualWriteCoordinator
from evidence_rag.repository import RepositorySnapshot
from evidence_rag.runtime import create_runtime
from evidence_rag.storage import SQLiteStore


class _CountingParser(CodeParser):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.parsed_files: list[ParsedFile] = []

    def parse(
        self,
        relative_path: str,
        content: str,
        blob_hash: str | None = None,
    ) -> ParsedFile:
        self.calls.append(relative_path)
        parsed = super().parse(relative_path, content, blob_hash)
        self.parsed_files.append(parsed)
        return parsed


class _FakeResolver:
    def __init__(self, root: Path, content: str) -> None:
        self.root = root
        self.files = {"src/app.py": content}
        self.snapshot = RepositorySnapshot(
            id="repo://dual-write",
            project_id="project://dual-write",
            name="dual-write",
            source_type="git_local",
            source_url="https://example.invalid/dual-write.git",
            local_path=root,
            default_branch="main",
            head_commit="commit-a",
            base_commit="commit-a",
            dirty=False,
            acl_ref="acl://dual-write",
        )

    def resolve(
        self,
        source: str,
        *,
        project_id: str,
        acl_ref: str,
        branch: str | None,
        history_depth: int,
    ) -> RepositorySnapshot:
        assert source == str(self.root)
        assert project_id == self.snapshot.project_id
        assert acl_ref == self.snapshot.acl_ref
        assert branch in {None, self.snapshot.default_branch}
        assert history_depth == 7
        return self.snapshot

    def discover_files(
        self,
        snapshot: RepositorySnapshot,
        ignore: list[str],
    ) -> list[Path]:
        assert snapshot is self.snapshot
        assert ignore == ["generated/"]
        return [self.root / path for path in sorted(self.files)]

    def read_text(self, path: Path) -> str | None:
        return self.files[path.relative_to(self.root).as_posix()]

    def blob_hash(
        self,
        snapshot: RepositorySnapshot,
        path: Path,
        content: str,
    ) -> str:
        assert snapshot is self.snapshot
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return f"blob-{digest}"

    def advance(self, *, commit: str, content: str) -> None:
        self.snapshot.head_commit = commit
        self.snapshot.base_commit = commit
        self.files["src/app.py"] = content


class _FakeRawStore:
    def __init__(self) -> None:
        self.derivations: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.fail_derivations = False

    def link_derivations(self, *args: Any, **kwargs: Any) -> None:
        if self.fail_derivations:
            raise RuntimeError("injected derivation failure")
        self.derivations.append((args, kwargs))


class _FakeSources:
    def __init__(self) -> None:
        self.store = _FakeRawStore()
        self.snapshot_raw_ids: list[str] = []
        self.raw_by_path: dict[str, str] = {}

    def accept(self, event: Any) -> dict[str, Any]:
        raw_id = f"raw://snapshot/{event.source_version}"
        self.snapshot_raw_ids.append(raw_id)
        return {"raw_object": {"id": raw_id}}

    def persist_bytes(self, **values: Any) -> dict[str, Any]:
        path = str(values["source_object_id"])
        raw_id = f"raw://file/{values['source_version']}/{path}"
        self.raw_by_path[path] = raw_id
        return {"id": raw_id}


class _FakeHistory:
    def sync(self, repository_id: str, **options: Any) -> dict[str, Any]:
        assert repository_id == "repo://dual-write"
        assert options == {
            "depth": 7,
            "include_diffs": True,
            "fetch_remote": False,
        }
        return {
            "repository_id": repository_id,
            "status": "complete",
            "commits": 2,
            "branches": 1,
            "hunks": 3,
        }


def _request(root: Path) -> RepositoryIngestRequest:
    return RepositoryIngestRequest(
        source=str(root),
        branch="main",
        project_id="project://dual-write",
        acl_ref="acl://dual-write",
        ignore=["generated/"],
        history_depth=7,
    )


def _service(
    root: Path,
    *,
    ast_v2: bool,
) -> tuple[
    SQLiteStore,
    _FakeResolver,
    _CountingParser,
    _FakeSources,
    IngestionService,
    CodeDualWriteCoordinator | None,
]:
    root.mkdir(parents=True, exist_ok=True)
    content = (
        "class Calculator:\n"
        "    def answer(self, value: int) -> int:\n"
        "        if value:\n"
        "            return value + 1\n"
        "        return 0\n\n"
        'if __name__ == "__main__":\n'
        "    print(Calculator().answer(1))\n"
    )
    store = SQLiteStore(root / "dual-write.sqlite3")
    store.initialize()
    resolver = _FakeResolver(root, content)
    parser = _CountingParser()
    sources = _FakeSources()
    coordinator = CodeDualWriteCoordinator(store) if ast_v2 else None
    service = IngestionService(
        store=store,
        resolver=resolver,  # type: ignore[arg-type]
        parser=parser,
        embedder=LocalHashEmbedding(32),
        sources=sources,  # type: ignore[arg-type]
        history=_FakeHistory(),  # type: ignore[arg-type]
        code_dual_writer=coordinator,
    )
    return store, resolver, parser, sources, service, coordinator


def _run(
    service: IngestionService,
    request: RepositoryIngestRequest,
) -> dict[str, Any]:
    workflow_id = service.enqueue(request)
    service.run(workflow_id, request)
    workflow = service.store.get_workflow(workflow_id)
    assert workflow is not None
    return workflow


def _table_count(store: SQLiteStore, table: str) -> int:
    with store.connection() as db:
        return int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_default_off_has_zero_c2_writes_and_ast_v2_preserves_legacy_counters(
    tmp_path: Path,
) -> None:
    off = _service(tmp_path / "off", ast_v2=False)
    on = _service(tmp_path / "on", ast_v2=True)
    off_store, off_resolver, off_parser, _, off_service, _ = off
    on_store, on_resolver, on_parser, on_sources, on_service, _ = on

    off_workflow = _run(off_service, _request(off_resolver.root))
    on_workflow = _run(on_service, _request(on_resolver.root))

    assert off_workflow["status"] == on_workflow["status"] == "completed"
    assert off_workflow["counters"] == on_workflow["counters"]
    assert off_parser.calls == on_parser.calls == ["src/app.py"]
    for table in ("code_retrieval_units", "code_index_publications"):
        assert _table_count(off_store, table) == 0

    assert _table_count(on_store, "code_retrieval_units") > 0
    assert _table_count(on_store, "code_index_publications") == 1
    generation_id = on_workflow["generation_id"]
    publication = on_store.get_code_index_publication(
        generation_id,
        repository_id=on_resolver.snapshot.id,
        active_only=True,
    )
    assert publication is not None
    assert publication["status"] == "published"
    assert publication["builder"].startswith("c2-code-retrieval-unit-builder-")
    assert publication["sparse"] == "fts5-code-v2"
    assert publication["embedding"] == "not-built"
    assert publication["graph"] == "not-built"
    assert publication["validation"]["capabilities"] == {
        "ast_unit_builder": True,
        "unit_store": True,
        "fts_row_integrity": True,
        "sparse_retrieval": True,
        "dense_retrieval": False,
        "graph_retrieval": False,
    }
    assert publication["validation"]["sparse_publication"] == {
        "schema_version": "code-sparse-publication-v1",
        "retriever_version": "code-exact-sparse-v1",
        "sparse_index_version": "fts5-code-v2",
        "unit_count": publication["validation"]["integrity"]["units"],
        "fts_row_count": publication["validation"]["integrity"]["fts_indexed_units"],
        "production_ingestion": True,
    }
    assert publication["validation"]["integrity"]["publications"] == 1
    assert publication["validation"]["integrity"]["healthy"] is True
    assert len(on_sources.store.derivations) == 2


def test_real_parser_ir_to_builder_store_retains_acl_version_and_lineage(
    tmp_path: Path,
) -> None:
    store, resolver, parser, sources, service, _ = _service(tmp_path, ast_v2=True)
    request = _request(resolver.root)
    workflow = _run(service, request)
    generation_id = workflow["generation_id"]

    assert parser.calls == ["src/app.py"]
    assert parser.parsed_files[0].units
    with store.connection() as db:
        row = db.execute(
            """
            SELECT u.*, e.id AS expected_entity_id
            FROM code_retrieval_units u
            JOIN entities e
              ON e.id=u.entity_id AND e.generation_id=u.generation_id
            WHERE u.generation_id=?
            ORDER BY u.start_byte, u.id
            LIMIT 1
            """,
            (generation_id,),
        ).fetchone()
    assert row is not None
    unit = dict(row)
    metadata = json.loads(unit["metadata_json"])
    lineage = metadata["source_lineage"]
    attributes = lineage["attributes"]
    assert unit["entity_id"] == unit["expected_entity_id"]
    assert unit["project_id"] == request.project_id
    assert unit["repository_id"] == resolver.snapshot.id
    assert unit["generation_id"] == generation_id
    assert unit["acl_ref"] == request.acl_ref
    assert metadata["ref"] == resolver.snapshot.head_commit
    assert lineage["ref"] == resolver.snapshot.head_commit
    assert lineage["blob_hash"].startswith("blob-")
    assert lineage["file_content_hash"].startswith("sha256:")
    assert attributes["entity_id"] == unit["entity_id"]
    assert attributes["generation_id"] == generation_id
    assert attributes["raw_object_id"] == sources.raw_by_path["src/app.py"]
    assert attributes["snapshot_raw_object_id"] == sources.snapshot_raw_ids[-1]
    assert attributes["request_source"] == request.source
    assert attributes["request_branch"] == request.branch
    assert attributes["snapshot_head_commit"] == resolver.snapshot.head_commit
    assert attributes["workflow_id"] == workflow["id"]


def test_symbol_and_nested_units_project_to_smallest_legacy_symbol(
    tmp_path: Path,
) -> None:
    store, resolver, _, _, service, _ = _service(tmp_path, ast_v2=True)
    workflow = _run(service, _request(resolver.root))
    generation_id = workflow["generation_id"]

    with store.connection() as db:
        entity_rows = db.execute(
            """
            SELECT id, entity_type, name, qualified_name, start_line, end_line
            FROM entities
            WHERE generation_id=? AND path='src/app.py'
            """,
            (generation_id,),
        ).fetchall()
        unit_rows = db.execute(
            """
            SELECT id, entity_id, unit_type, ast_node_type, qualified_name,
                   start_line, end_line, context_ref_json, metadata_json
            FROM code_retrieval_units
            WHERE generation_id=? AND path='src/app.py'
            ORDER BY start_line, end_line DESC, id
            """,
            (generation_id,),
        ).fetchall()
    entities = [dict(row) for row in entity_rows]
    units = [dict(row) for row in unit_rows]
    file_entity = next(item for item in entities if item["entity_type"] == "FileVersion")
    calculator = next(item for item in entities if item["name"] == "Calculator")
    answer = next(item for item in entities if item["name"] == "answer")
    file_unit = next(item for item in units if item["unit_type"] == "file.surface")
    class_unit = next(item for item in units if item["ast_node_type"] == "class_definition")
    function_unit = next(item for item in units if item["ast_node_type"] == "function_definition")
    nested_if = next(
        item
        for item in units
        if item["ast_node_type"] == "if_statement" and item["start_line"] == 3
    )
    top_level_if = next(
        item
        for item in units
        if item["ast_node_type"] == "if_statement" and item["start_line"] == 7
    )

    assert file_unit["entity_id"] == file_entity["id"]
    assert class_unit["entity_id"] == calculator["id"]
    assert function_unit["entity_id"] == answer["id"]
    assert nested_if["entity_id"] == answer["id"]
    assert top_level_if["entity_id"] == file_entity["id"]
    assert calculator["id"].find("#symbol=") > 0
    assert answer["id"].find("#symbol=") > 0
    assert class_unit["qualified_name"] == calculator["qualified_name"]
    assert function_unit["qualified_name"] == answer["qualified_name"]
    for unit in units:
        context_ref = json.loads(unit["context_ref_json"])
        metadata = json.loads(unit["metadata_json"])
        assert context_ref["parent_entity_id"] == unit["entity_id"]
        assert metadata["entity_projection"]["entity_id"] == unit["entity_id"]
    top_level_projection = json.loads(top_level_if["metadata_json"])["entity_projection"]
    assert top_level_projection == {
        "entity_id": file_entity["id"],
        "entity_type": "FileVersion",
        "reason": "no-containing-symbol",
    }

    publication = store.get_code_index_publication(
        generation_id,
        repository_id=resolver.snapshot.id,
        active_only=True,
    )
    assert publication is not None
    counts = publication["validation"]["foundation"]["entity_projection"]
    symbol_units = sum(
        json.loads(item["metadata_json"])["entity_projection"]["entity_type"] == "CodeSymbol"
        for item in units
    )
    assert counts["code_symbol_units"] == symbol_units
    assert counts["file_version_units"] == len(units) - symbol_units
    assert counts["reasons"]["no-containing-symbol"] == 2


def test_same_name_symbols_are_projected_by_span_without_cross_binding(
    tmp_path: Path,
) -> None:
    store, resolver, _, _, service, _ = _service(tmp_path, ast_v2=True)
    resolver.files["src/app.py"] = (
        "class Handler:\n"
        "    def run(self, value: int) -> int:\n"
        "        return value\n\n"
        "    def run(self, value: str) -> str:\n"
        "        return value.strip()\n"
    )
    workflow = _run(service, _request(resolver.root))
    generation_id = workflow["generation_id"]

    with store.connection() as db:
        symbols = [
            dict(row)
            for row in db.execute(
                """
                SELECT id, qualified_name, start_line, end_line
                FROM entities
                WHERE generation_id=? AND entity_type='CodeSymbol' AND name='run'
                ORDER BY start_line
                """,
                (generation_id,),
            ).fetchall()
        ]
        functions = [
            dict(row)
            for row in db.execute(
                """
                SELECT entity_id, qualified_name, start_line, end_line
                FROM code_retrieval_units
                WHERE generation_id=? AND ast_node_type='function_definition'
                ORDER BY start_line
                """,
                (generation_id,),
            ).fetchall()
        ]
        nested_blocks = [
            dict(row)
            for row in db.execute(
                """
                SELECT entity_id, start_line, end_line
                FROM code_retrieval_units
                WHERE generation_id=? AND ast_node_type='block'
                  AND start_line IN (3, 6)
                ORDER BY start_line
                """,
                (generation_id,),
            ).fetchall()
        ]

    assert len(symbols) == len(functions) == len(nested_blocks) == 2
    assert len({item["id"] for item in symbols}) == 2
    assert all("#symbol=" in item["id"] for item in symbols)
    assert len({item["qualified_name"] for item in symbols}) == 1
    for symbol, function, block in zip(symbols, functions, nested_blocks, strict=True):
        assert function["entity_id"] == symbol["id"]
        assert function["qualified_name"] == symbol["qualified_name"]
        assert function["start_line"] == symbol["start_line"]
        assert function["end_line"] == symbol["end_line"]
        assert block["entity_id"] == symbol["id"]


def test_same_generation_retry_is_idempotent_and_old_generation_is_not_active(
    tmp_path: Path,
) -> None:
    store, resolver, parser, sources, service, coordinator = _service(
        tmp_path,
        ast_v2=True,
    )
    assert coordinator is not None
    request = _request(resolver.root)
    first_workflow = _run(service, request)
    first_generation = first_workflow["generation_id"]
    parsed = tuple(parser.parsed_files)
    entities, _, _, _ = service._build_records(
        resolver.snapshot,
        first_generation,
        list(parsed),
    )
    retry_options = {
        "snapshot": resolver.snapshot,
        "request": request,
        "workflow_id": first_workflow["id"],
        "generation_id": first_generation,
        "parsed_files": parsed,
        "entities": entities,
        "raw_object_ids": dict(sources.raw_by_path),
        "snapshot_raw_object_id": sources.snapshot_raw_ids[-1],
    }
    before = _table_count(store, "code_retrieval_units")
    first_retry = coordinator.write(**retry_options)
    second_retry = coordinator.write(**retry_options)

    assert _table_count(store, "code_retrieval_units") == before
    assert first_retry.unit_count == second_retry.unit_count == before
    assert first_retry.publication_hash == second_retry.publication_hash
    assert parser.calls == ["src/app.py"]

    resolver.advance(
        commit="commit-b",
        content=("def answer(value: int) -> int:\n    return value + 2\n"),
    )
    second_workflow = _run(service, request)
    second_generation = second_workflow["generation_id"]
    assert second_generation != first_generation
    assert parser.calls == ["src/app.py", "src/app.py"]
    assert store.active_code_generations() == {
        resolver.snapshot.id: second_generation,
    }
    active = store.exact_code_unit_lookup(
        path="src/app.py",
        repository_ids=[resolver.snapshot.id],
        active_only=True,
    )
    assert active
    assert {item["generation_id"] for item in active} == {second_generation}
    assert (
        store.get_code_index_publication(
            first_generation,
            repository_id=resolver.snapshot.id,
            active_only=True,
        )
        is None
    )
    assert store.get_code_index_publication(
        first_generation,
        repository_id=resolver.snapshot.id,
        active_only=False,
    )


def test_dual_write_failure_cleans_partial_c2_and_keeps_legacy_failure_handling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, resolver, parser, _, service, _ = _service(tmp_path, ast_v2=True)
    original_publication = store.upsert_code_index_publication

    def fail_after_publication(record: dict[str, Any]) -> dict[str, Any]:
        original_publication(record)
        raise RuntimeError("injected publication failure")

    monkeypatch.setattr(store, "upsert_code_index_publication", fail_after_publication)
    workflow = _run(service, _request(resolver.root))

    assert workflow["status"] == "failed"
    assert workflow["stage"] == "failed"
    assert "injected publication failure" in workflow["error"]
    assert parser.calls == ["src/app.py"]
    for table in (
        "code_retrieval_units",
        "code_unit_vectors",
        "code_relation_diagnostics",
        "code_index_publications",
    ):
        assert _table_count(store, table) == 0
    with store.connection() as db:
        generation = db.execute(
            "SELECT status FROM index_generations WHERE id=?",
            (workflow["generation_id"],),
        ).fetchone()
        repository = db.execute(
            "SELECT status FROM repositories WHERE id=?",
            (resolver.snapshot.id,),
        ).fetchone()
        legacy_entities = db.execute(
            "SELECT count(*) FROM entities WHERE generation_id=?",
            (workflow["generation_id"],),
        ).fetchone()[0]
    assert generation["status"] == "failed"
    assert repository["status"] == "failed"
    assert legacy_entities > 0


@pytest.mark.parametrize("failure_stage", ["derivation", "finalize"])
def test_post_publication_failure_rolls_back_all_c2_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    store, resolver, _, sources, service, _ = _service(tmp_path, ast_v2=True)
    published_statuses: list[str] = []
    original_publication = store.upsert_code_index_publication

    def record_publication(record: dict[str, Any]) -> dict[str, Any]:
        published_statuses.append(str(record["status"]))
        return original_publication(record)

    monkeypatch.setattr(store, "upsert_code_index_publication", record_publication)
    if failure_stage == "derivation":
        sources.store.fail_derivations = True
    else:
        original_update = store.update_workflow

        def fail_finalize(workflow_id: str, **values: Any) -> None:
            if values.get("status") == "completed":
                original_update(workflow_id, **values)
                raise RuntimeError("injected finalize failure")
            original_update(workflow_id, **values)

        monkeypatch.setattr(store, "update_workflow", fail_finalize)

    workflow = _run(service, _request(resolver.root))
    generation_id = workflow["generation_id"]

    assert published_statuses == ["building", "published"]
    assert workflow["status"] == "failed"
    assert workflow["stage"] == "failed"
    assert f"injected {failure_stage} failure" in workflow["error"]
    assert store.code_v2_integrity_stats(
        repository_id=resolver.snapshot.id,
        generation_id=generation_id,
    ) == {
        "units": 0,
        "active_units": 0,
        "vectors": 0,
        "units_without_vectors": 0,
        "diagnostics": 0,
        "publications": 0,
        "fts_indexed_units": 0,
        "violations": {
            "foreign_keys": 0,
            "identity": 0,
            "vector_content_hash": 0,
            "fts_index": 0,
        },
        "healthy": True,
    }
    assert (
        store.get_code_index_publication(
            generation_id,
            repository_id=resolver.snapshot.id,
            active_only=False,
        )
        is None
    )
    with store.connection() as db:
        generation_status = db.execute(
            "SELECT status FROM index_generations WHERE id=?",
            (generation_id,),
        ).fetchone()["status"]
    assert generation_status == "failed"


def test_cleanup_failure_keeps_original_ingestion_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, resolver, _, sources, service, coordinator = _service(tmp_path, ast_v2=True)
    assert coordinator is not None
    sources.store.fail_derivations = True

    def fail_cleanup(**_scope: str) -> dict[str, int]:
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(coordinator, "rollback", fail_cleanup)
    workflow = _run(service, _request(resolver.root))

    assert workflow["status"] == "failed"
    assert workflow["error"].startswith("RuntimeError: injected derivation failure")
    assert "Code V2 cleanup failed: RuntimeError: injected cleanup failure" in workflow["error"]


def test_runtime_constructs_dual_writer_only_for_ast_v2(settings, tmp_path: Path) -> None:
    default_runtime = create_runtime(settings)
    assert default_runtime.ingestion.code_dual_writer is None

    ast_data = tmp_path / "ast-runtime"
    ast_settings = replace(
        settings,
        data_dir=ast_data,
        database_path=ast_data / "runtime.sqlite3",
        repository_cache=ast_data / "repositories",
        rag_code_unit_builder="ast-v2",
    )
    ast_runtime = create_runtime(ast_settings)
    assert isinstance(
        ast_runtime.ingestion.code_dual_writer,
        CodeDualWriteCoordinator,
    )
