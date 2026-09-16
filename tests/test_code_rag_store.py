from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from evidence_rag.bindings.schema import SCHEMA as BINDINGS_SCHEMA
from evidence_rag.code_history.schema import SCHEMA as CODE_HISTORY_SCHEMA
from evidence_rag.db_schema import BASE_SCHEMA
from evidence_rag.documents.schema import SCHEMA as DOCUMENTS_SCHEMA
from evidence_rag.experiments.schema import SCHEMA as EXPERIMENTS_SCHEMA
from evidence_rag.platform.schema import SCHEMA as PLATFORM_SCHEMA
from evidence_rag.sources.schema import SCHEMA as SOURCES_SCHEMA
from evidence_rag.storage import SQLiteStore
from evidence_rag.workspace.schema import WORKSPACE_SCHEMA


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "code-v2.sqlite3")
    store.initialize()
    return store


def _seed_repository(
    store: SQLiteStore,
    *,
    repository_id: str = "repository://alpha",
    project_id: str = "project://alpha",
    generation_id: str = "generation://alpha-active",
    entity_id: str = "entity://alpha-active",
    acl_ref: str = "acl://alpha",
    active: bool = True,
) -> None:
    with store.transaction() as db:
        db.execute(
            """
            INSERT INTO repositories(
                id, project_id, name, source_type, local_path, head_commit, acl_ref,
                status, active_generation_id, created_at, updated_at
            ) VALUES (?, ?, ?, 'local', ?, 'abc123', ?, 'ready', ?, 'now', 'now')
            ON CONFLICT(id) DO UPDATE SET
                active_generation_id=CASE
                    WHEN excluded.active_generation_id IS NULL
                    THEN repositories.active_generation_id
                    ELSE excluded.active_generation_id
                END
            """,
            (
                repository_id,
                project_id,
                repository_id,
                f"/tmp/{repository_id.rsplit('/', 1)[-1]}",
                acl_ref,
                generation_id if active else None,
            ),
        )
        db.execute(
            """
            INSERT INTO index_generations(
                id, repository_id, commit_sha, status, started_at, completed_at
            ) VALUES (?, ?, 'abc123', 'published', 'now', 'now')
            """,
            (generation_id, repository_id),
        )
        db.execute(
            """
            INSERT INTO entities(
                id, repository_id, generation_id, project_id, entity_type, name,
                qualified_name, path, language, commit_sha, content_hash, source_uri,
                acl_ref, content
            ) VALUES (?, ?, ?, ?, 'CodeSymbol', ?, ?, 'src/example.py', 'python',
                      'abc123', ?, ?, ?, 'def needle(): pass')
            """,
            (
                entity_id,
                repository_id,
                generation_id,
                project_id,
                entity_id,
                entity_id,
                f"hash-{entity_id}",
                f"code://{entity_id}",
                acl_ref,
            ),
        )


def _unit(
    *,
    unit_id: str = "unit://alpha-active",
    entity_id: str = "entity://alpha-active",
    repository_id: str = "repository://alpha",
    generation_id: str = "generation://alpha-active",
    project_id: str = "project://alpha",
    acl_ref: str = "acl://alpha",
    qualified_name: str = "package.needle",
    body: str = "def needle(): return exact_value",
) -> dict[str, Any]:
    return {
        "id": unit_id,
        "entity_id": entity_id,
        "parent_unit_id": None,
        "repository_id": repository_id,
        "generation_id": generation_id,
        "project_id": project_id,
        "unit_type": "symbol",
        "ast_node_type": "function_definition",
        "ordinal": 0,
        "language": "python",
        "path": "src/example.py",
        "qualified_name": qualified_name,
        "signature": "needle(value: int) -> int",
        "identifiers": ["needle", "exact_value"],
        "doc": "Return the exact value.",
        "body": body,
        "content": body,
        "context_ref": {"parent": None, "siblings": []},
        "start_line": 1,
        "end_line": 2,
        "start_byte": 0,
        "end_byte": len(body.encode()),
        "token_count": 8,
        "content_hash": f"content-{unit_id}",
        "builder_version": "ast-v2-test",
        "quality_status": "ready",
        "acl_ref": acl_ref,
        "metadata": {"z": 1, "a": 2},
    }


def _vector(unit: MappingLike) -> dict[str, Any]:
    return {
        "unit_id": unit["id"],
        "generation_id": unit["generation_id"],
        "profile": "code-v2",
        "model": "local-test",
        "dimension": 2,
        "vector": b"\x00\x00\x80?\x00\x00\x00\x00",
        "content_hash": unit["content_hash"],
    }


MappingLike = dict[str, Any]


def _initialize_repository_delete_dependencies(store: SQLiteStore) -> None:
    with store.connection() as db:
        for schema in (
            WORKSPACE_SCHEMA,
            SOURCES_SCHEMA,
            BINDINGS_SCHEMA,
            EXPERIMENTS_SCHEMA,
            DOCUMENTS_SCHEMA,
            PLATFORM_SCHEMA,
            CODE_HISTORY_SCHEMA,
        ):
            db.executescript(schema)


def test_initialize_is_idempotent_and_creates_six_field_fts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.initialize()

    with store.connection() as db:
        tables = {
            row["name"]
            for row in db.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type IN ('table', 'trigger') AND name LIKE 'code_%'
                """
            )
        }
        fts_columns = [
            row["name"] for row in db.execute("PRAGMA table_info(code_retrieval_units_fts)")
        ]

    assert {
        "code_retrieval_units",
        "code_unit_vectors",
        "code_unit_embedding_cache",
        "code_relation_diagnostics",
        "code_retrieval_calibrations",
        "code_index_publications",
        "code_retrieval_units_ai",
        "code_retrieval_units_au",
        "code_retrieval_units_ad",
    } <= tables
    assert fts_columns == [
        "qualified_name",
        "signature",
        "path",
        "identifiers",
        "doc",
        "body",
    ]


def test_initialize_migrates_an_old_database_without_backfill(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as db:
        db.executescript(BASE_SCHEMA)
        db.execute(
            """
            INSERT INTO repositories(
                id, project_id, name, source_type, local_path, head_commit, acl_ref,
                status, created_at, updated_at
            ) VALUES (
                'repository://legacy', 'project://legacy', 'legacy', 'local',
                '/tmp/legacy', 'abc123', 'public', 'ready', 'now', 'now'
            )
            """
        )

    store = SQLiteStore(database_path)
    store.initialize()
    store.initialize()

    with store.connection() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM repositories WHERE id='repository://legacy'"
            ).fetchone()[0]
            == 1
        )
        assert db.execute("SELECT count(*) FROM code_retrieval_units").fetchone()[0] == 0


def test_insert_query_and_scope_isolation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_repository(store)
    _seed_repository(
        store,
        generation_id="generation://alpha-old",
        entity_id="entity://alpha-old",
        active=False,
    )
    _seed_repository(
        store,
        repository_id="repository://beta",
        generation_id="generation://beta",
        entity_id="entity://beta",
        acl_ref="acl://beta",
    )
    _seed_repository(
        store,
        repository_id="repository://gamma",
        project_id="project://other",
        generation_id="generation://gamma",
        entity_id="entity://gamma",
        acl_ref="acl://gamma",
    )
    units = [
        _unit(),
        _unit(
            unit_id="unit://alpha-old",
            entity_id="entity://alpha-old",
            generation_id="generation://alpha-old",
        ),
        _unit(
            unit_id="unit://beta",
            entity_id="entity://beta",
            repository_id="repository://beta",
            generation_id="generation://beta",
            acl_ref="acl://beta",
        ),
        _unit(
            unit_id="unit://gamma",
            entity_id="entity://gamma",
            repository_id="repository://gamma",
            generation_id="generation://gamma",
            project_id="project://other",
            acl_ref="acl://gamma",
        ),
    ]
    assert store.insert_code_units(units) == 4
    assert store.insert_code_unit_vectors([_vector(unit) for unit in units]) == 4

    alpha = store.search_code_units_sparse(
        {"qualified_name": "needle"},
        project_id="project://alpha",
        allowed_acl_refs=["acl://alpha"],
    )
    assert [item["id"] for item in alpha] == ["unit://alpha-active"]
    assert alpha[0]["metadata"] == {"a": 2, "z": 1}

    beta = store.exact_code_unit_lookup(
        "package.needle",
        repository_ids=["repository://beta"],
        allowed_acl_refs=["acl://beta"],
    )
    assert [item["id"] for item in beta] == ["unit://beta"]

    old = store.exact_code_unit_lookup(
        "package.needle",
        repository_ids=["repository://alpha"],
        generation_id="generation://alpha-old",
        allowed_acl_refs=["acl://alpha"],
        active_only=False,
    )
    assert [item["id"] for item in old] == ["unit://alpha-old"]
    assert (
        store.exact_code_unit_lookup(
            "package.needle",
            repository_ids=["repository://alpha"],
            generation_id="generation://alpha-old",
            allowed_acl_refs=["acl://alpha"],
        )
        == []
    )

    vectors = list(
        store.iter_code_unit_vectors(
            project_id="project://alpha",
            allowed_acl_refs=["acl://beta"],
            profile="code-v2",
        )
    )
    assert [item["unit_id"] for item in vectors] == ["unit://beta"]
    assert store.active_code_generations(project_id="project://alpha") == {
        "repository://alpha": "generation://alpha-active",
        "repository://beta": "generation://beta",
    }


def test_fts_triggers_follow_insert_update_and_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_repository(store)
    unit = _unit(body="def needle(): return before_update")
    store.insert_code_units([unit])
    assert store.search_code_units_sparse({"body": "before_update"})

    unit["body"] = "def needle(): return after_update"
    unit["content"] = unit["body"]
    store.upsert_code_units([unit])
    assert store.search_code_units_sparse({"body": "before_update"}) == []
    assert [item["id"] for item in store.search_code_units_sparse({"body": "after_update"})] == [
        unit["id"]
    ]

    with store.transaction() as db:
        db.execute(
            "DELETE FROM code_retrieval_units WHERE id=? AND generation_id=?",
            (unit["id"], unit["generation_id"]),
        )
    assert store.search_code_units_sparse({"body": "after_update"}) == []


def test_batch_write_rolls_back_on_foreign_identity_failure(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_repository(store)
    valid = _unit(unit_id="unit://would-have-been-written")
    invalid = _unit(unit_id="unit://invalid", entity_id="entity://missing")

    with pytest.raises(sqlite3.IntegrityError):
        store.insert_code_units([valid, invalid])

    with store.connection() as db:
        assert (
            db.execute(
                """
            SELECT count(*) FROM code_retrieval_units
            WHERE generation_id='generation://alpha-active'
            """
            ).fetchone()[0]
            == 0
        )


def test_publication_upsert_context_and_generation_cleanup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_repository(store)
    parent = _unit(unit_id="unit://parent")
    child = _unit(unit_id="unit://child")
    child["parent_unit_id"] = parent["id"]
    child["ordinal"] = 1
    store.insert_code_units([child, parent])
    store.insert_code_unit_vectors([_vector(parent), _vector(child)])
    store.insert_code_relation_diagnostics(
        [
            {
                "repository_id": parent["repository_id"],
                "generation_id": parent["generation_id"],
                "project_id": parent["project_id"],
                "source_entity_id": parent["entity_id"],
                "relation_type": "CALLS",
                "raw_target": "missing_target",
                "reason": "unresolved",
                "candidates": [{"name": "candidate_b"}, {"name": "candidate_a"}],
                "parser": "tree-sitter-test",
                "acl_ref": parent["acl_ref"],
            }
        ]
    )
    publication = store.upsert_code_index_publication(
        {
            "generation_id": parent["generation_id"],
            "repository_id": parent["repository_id"],
            "project_id": parent["project_id"],
            "builder": "ast-v2-test",
            "sparse": "fts5-v2",
            "embedding": "local-test",
            "graph": "typed-v2",
            "status": "building",
            "validation": {"z": 1, "a": 2},
        }
    )
    assert publication["validation"] == {"a": 2, "z": 1}
    publication["status"] = "published"
    updated = store.upsert_code_index_publication(publication)
    assert updated["status"] == "published"
    assert store.get_code_index_publication(parent["generation_id"], active_only=True) == updated

    assert (
        store.upsert_code_embedding_cache(
            [
                {
                    "cache_key": "sha256:content",
                    "profile": "code-v2",
                    "model": "local-test",
                    "dimension": 2,
                    "vector": b"\x00" * 8,
                }
            ]
        )
        == 1
    )
    cached = store.get_code_embedding_cache(
        "sha256:content",
        profile="code-v2",
        model="local-test",
        touch=True,
    )
    assert cached is not None
    assert cached["vector"] == b"\x00" * 8
    assert (
        store.upsert_code_retrieval_calibrations(
            [
                {
                    "id": "calibration://code-v2",
                    "profile": "code-v2",
                    "task": "implementation",
                    "entity_type": "CodeSymbol",
                    "model": "local-test",
                    "artifact_ref": "artifact://calibration",
                    "metrics": {"ece": 0.02},
                    "status": "active",
                }
            ]
        )
        == 1
    )
    calibration = store.get_code_retrieval_calibration("calibration://code-v2")
    assert calibration is not None
    assert calibration["metrics"] == {"ece": 0.02}

    context = store.load_code_unit_context(parent["id"])
    assert context is not None
    assert context["entity"]["id"] == parent["entity_id"]
    assert [item["id"] for item in context["children"]] == [child["id"]]

    counts = store.cleanup_code_generation(
        parent["generation_id"],
        repository_id=parent["repository_id"],
    )
    assert counts == {"units": 2, "vectors": 2, "diagnostics": 1, "publications": 1}
    assert store.load_code_unit(parent["id"]) is None
    with store.connection() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM entities WHERE generation_id=?",
                (parent["generation_id"],),
            ).fetchone()[0]
            == 1
        )


def test_repository_delete_cascades_code_v2_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _initialize_repository_delete_dependencies(store)
    _seed_repository(store)
    unit = _unit()
    store.insert_code_units([unit])
    store.insert_code_unit_vectors([_vector(unit)])
    store.insert_code_relation_diagnostics(
        [
            {
                "id": "diagnostic://alpha",
                "repository_id": unit["repository_id"],
                "generation_id": unit["generation_id"],
                "project_id": unit["project_id"],
                "source_entity_id": unit["entity_id"],
                "relation_type": "REFERENCES",
                "raw_target": "unknown",
                "reason": "unresolved",
                "candidates": [],
                "parser": "test",
                "acl_ref": unit["acl_ref"],
            }
        ]
    )
    store.upsert_code_index_publication(
        {
            "generation_id": unit["generation_id"],
            "repository_id": unit["repository_id"],
            "project_id": unit["project_id"],
            "builder": "ast-v2-test",
            "sparse": "fts5-v2",
            "embedding": "local-test",
            "graph": "typed-v2",
            "status": "published",
            "validation": {},
        }
    )

    deleted = store.delete_repository(unit["repository_id"])
    assert deleted is not None
    assert deleted["deleted"] is True
    with store.connection() as db:
        for table in (
            "code_retrieval_units",
            "code_unit_vectors",
            "code_relation_diagnostics",
            "code_index_publications",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert (
            db.execute("SELECT count(*) FROM code_retrieval_units_fts_docsize").fetchone()[0] == 0
        )


def test_context_expansion_preserves_project_generation_repository_and_acl_scope(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_repository(store)
    with store.transaction() as db:
        db.executemany(
            """
            INSERT INTO entities(
                id, repository_id, generation_id, project_id, entity_type, name,
                qualified_name, path, language, commit_sha, content_hash, source_uri,
                acl_ref, content
            ) VALUES (?, 'repository://alpha', 'generation://alpha-active', ?,
                      'CodeSymbol', ?, ?, 'src/example.py', 'python', 'abc123', ?,
                      ?, ?, 'def needle(): pass')
            """,
            [
                (
                    "entity://secret",
                    "project://alpha",
                    "secret",
                    "secret",
                    "hash-secret",
                    "code://secret",
                    "acl://secret",
                ),
                (
                    "entity://other-project",
                    "project://other",
                    "other_project",
                    "other_project",
                    "hash-other-project",
                    "code://other-project",
                    "acl://alpha",
                ),
            ],
        )

    target = _unit(unit_id="unit://target")
    secret_parent = _unit(
        unit_id="unit://secret-parent",
        entity_id="entity://secret",
        acl_ref="acl://secret",
    )
    target["parent_unit_id"] = secret_parent["id"]
    allowed_child = _unit(unit_id="unit://allowed-child")
    allowed_child["parent_unit_id"] = target["id"]
    allowed_child["ordinal"] = 1
    secret_child = _unit(
        unit_id="unit://secret-child",
        entity_id="entity://secret",
        acl_ref="acl://secret",
    )
    secret_child["parent_unit_id"] = target["id"]
    secret_child["ordinal"] = 2
    other_project_child = _unit(
        unit_id="unit://other-project-child",
        entity_id="entity://other-project",
        project_id="project://other",
    )
    other_project_child["parent_unit_id"] = target["id"]
    other_project_child["ordinal"] = 3
    store.insert_code_units(
        [target, secret_parent, allowed_child, secret_child, other_project_child]
    )

    context = store.load_code_unit_context(
        target["id"],
        project_id=target["project_id"],
        repository_id=target["repository_id"],
        generation_id=target["generation_id"],
        allowed_acl_refs=[target["acl_ref"]],
        active_only=True,
    )

    assert context is not None
    assert context["parent"] is None
    assert [item["id"] for item in context["children"]] == [allowed_child["id"]]
    assert {
        (item["project_id"], item["repository_id"], item["generation_id"], item["acl_ref"])
        for item in context["children"]
    } == {
        (
            target["project_id"],
            target["repository_id"],
            target["generation_id"],
            target["acl_ref"],
        )
    }


def test_publication_rejects_repository_project_mismatch_before_write(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    _seed_repository(store)
    publication = {
        "generation_id": "generation://alpha-active",
        "repository_id": "repository://alpha",
        "project_id": "project://wrong",
        "builder": "ast-v2-test",
        "sparse": "fts5-v2",
        "embedding": "local-test",
        "graph": "typed-v2",
        "status": "published",
        "validation": {},
    }

    with pytest.raises(ValueError, match="repository/project mismatch"):
        store.upsert_code_index_publication(publication)
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM code_index_publications").fetchone()[0] == 0

    publication["project_id"] = "project://alpha"
    stored = store.upsert_code_index_publication(publication)
    assert stored["repository_id"] == "repository://alpha"
    assert stored["project_id"] == "project://alpha"


def test_generation_delete_flow_cascades_code_v2_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_repository(store)
    unit = _unit()
    store.insert_code_units([unit])
    store.insert_code_unit_vectors([_vector(unit)])
    store.upsert_code_index_publication(
        {
            "generation_id": unit["generation_id"],
            "repository_id": unit["repository_id"],
            "project_id": unit["project_id"],
            "builder": "ast-v2-test",
            "sparse": "fts5-v2",
            "embedding": "local-test",
            "graph": "typed-v2",
            "status": "published",
            "validation": {},
        }
    )

    with store.transaction() as db:
        db.execute(
            "UPDATE repositories SET active_generation_id=NULL WHERE id=?",
            (unit["repository_id"],),
        )
        db.execute("DELETE FROM entities WHERE generation_id=?", (unit["generation_id"],))
        db.execute("DELETE FROM index_generations WHERE id=?", (unit["generation_id"],))

    with store.connection() as db:
        for table in (
            "code_retrieval_units",
            "code_unit_vectors",
            "code_relation_diagnostics",
            "code_index_publications",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_integrity_stats_report_counts_and_vector_hash_mismatch(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed_repository(store)
    unit = _unit()
    store.insert_code_units([unit])
    store.insert_code_unit_vectors([_vector(unit)])
    store.upsert_code_index_publication(
        {
            "generation_id": unit["generation_id"],
            "repository_id": unit["repository_id"],
            "project_id": unit["project_id"],
            "builder": "ast-v2-test",
            "sparse": "fts5-v2",
            "embedding": "local-test",
            "graph": "typed-v2",
            "status": "published",
            "validation": {"ok": True},
        }
    )

    stats = store.code_v2_integrity_stats()
    assert stats == {
        "units": 1,
        "active_units": 1,
        "vectors": 1,
        "units_without_vectors": 0,
        "diagnostics": 0,
        "publications": 1,
        "fts_indexed_units": 1,
        "violations": {
            "foreign_keys": 0,
            "identity": 0,
            "vector_content_hash": 0,
            "fts_index": 0,
        },
        "healthy": True,
    }

    with store.transaction() as db:
        db.execute(
            """
            UPDATE code_unit_vectors SET content_hash='stale'
            WHERE unit_id=? AND generation_id=?
            """,
            (unit["id"], unit["generation_id"]),
        )
    degraded = store.code_v2_integrity_stats(
        repository_id=unit["repository_id"],
        generation_id=unit["generation_id"],
    )
    assert degraded["violations"]["vector_content_hash"] == 1
    assert degraded["healthy"] is False
