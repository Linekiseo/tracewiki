"""Fail-closed isolated Workspace V2 store."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

from .contracts import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceEdge,
    WorkspaceEntity,
    WorkspaceEntityType,
    WorkspacePublication,
    WorkspaceRetrievalUnit,
    WorkspaceScope,
    WorkspaceTombstone,
    canonical_json_bytes,
)
from .schema import (
    WORKSPACE_ENTITY_TABLES,
    WORKSPACE_SCHEMA_STATEMENTS,
    workspace_typed_table_statement,
)


class WorkspaceStoreError(ValueError):
    """Base isolated Workspace store error."""


class WorkspaceStorePathError(WorkspaceStoreError):
    """Raised when an isolated store path is unsafe."""


class WorkspacePublicationError(WorkspaceStoreError):
    """Raised when atomic publication cannot be accepted."""


def _path_has_symlink(path: Path, stop: Path) -> bool:
    current = path
    while current != stop:
        if current.exists() and current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return stop.exists() and stop.is_symlink()


class WorkspaceV2Store:
    def __init__(self, database_path: Path, *, isolated_root: Path) -> None:
        if not database_path.is_absolute() or not isolated_root.is_absolute():
            raise WorkspaceStorePathError("Workspace store paths must be absolute")
        if database_path.name in {
            "evidence-rag.sqlite3",
            "evidence-rag.sqlite3-wal",
            "evidence-rag.sqlite3-shm",
        }:
            raise WorkspaceStorePathError("formal service database is forbidden")
        raw_root = Path(os.path.abspath(isolated_root))
        raw_path = Path(os.path.abspath(database_path))
        if raw_path == raw_root or not raw_path.is_relative_to(raw_root):
            raise WorkspaceStorePathError("Workspace store must be inside isolated root")
        if _path_has_symlink(raw_path.parent, raw_root):
            raise WorkspaceStorePathError("Workspace store path traverses a symlink")
        root = raw_root.resolve(strict=False)
        path = raw_path.resolve(strict=False)
        if path == root or not path.is_relative_to(root):
            raise WorkspaceStorePathError("Workspace store must be inside isolated root")
        root.mkdir(parents=True, exist_ok=True)
        if path.exists():
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or path.is_symlink():
                raise WorkspaceStorePathError("Workspace store must be a regular file")
        for suffix in ("-wal", "-shm"):
            if Path(str(path) + suffix).exists():
                raise WorkspaceStorePathError("Workspace store sidecars are forbidden")
        self.path = path
        self.root = root

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=MEMORY")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            for statement in WORKSPACE_SCHEMA_STATEMENTS:
                connection.execute(statement)
            for table in WORKSPACE_ENTITY_TABLES.values():
                connection.execute(workspace_typed_table_statement(table))
            connection.execute(
                """
                INSERT OR IGNORE INTO workspace_schema_meta_v2(schema_version, installed_at)
                VALUES (?, ?)
                """,
                (WORKSPACE_SCHEMA_VERSION, "2026-07-29T00:00:00Z"),
            )
        self._assert_no_sidecars()

    def _assert_no_sidecars(self) -> None:
        if any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise WorkspaceStoreError("Workspace isolated store created sidecars")

    def publish(self, publication: WorkspacePublication) -> None:
        self.initialize()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT publication_sha256
                FROM workspace_publications_v2
                WHERE publication_id=?
                """,
                (publication.publication_id,),
            ).fetchone()
            if existing:
                if existing["publication_sha256"] != publication.publication_sha256:
                    raise WorkspacePublicationError(
                        "publication identity already exists with different content"
                    )
                return
            tombstoned = {
                row["target_entity_id"]
                for row in connection.execute(
                    "SELECT target_entity_id FROM workspace_tombstones_v2 WHERE project_id=?",
                    (publication.scope.project_id,),
                )
            }
            if tombstoned & {item.entity_id for item in publication.entities}:
                raise WorkspacePublicationError("publication attempts to revive tombstoned entity")
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE workspace_publications_v2
                    SET active=0
                    WHERE project_id=? AND active=1
                    """,
                    (publication.scope.project_id,),
                )
                connection.execute(
                    """
                    INSERT INTO workspace_publications_v2(
                        publication_id, project_id, acl_ref, generation_id,
                        publication_sha256, publication_json, published_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        publication.publication_id,
                        publication.scope.project_id,
                        publication.scope.acl_ref,
                        publication.scope.generation_id,
                        publication.publication_sha256,
                        canonical_json_bytes(publication.model_dump(mode="json")).decode(),
                        publication.published_at,
                    ),
                )
                for entity in publication.entities:
                    self._insert_entity(connection, publication.publication_id, entity)
                for edge in publication.edges:
                    self._insert_edge(connection, publication.publication_id, edge)
                for unit in publication.retrieval_units:
                    self._insert_unit(connection, publication.publication_id, unit)
            except sqlite3.IntegrityError as error:
                raise WorkspacePublicationError(
                    "Workspace publication failed atomically"
                ) from error
        self._assert_no_sidecars()

    @staticmethod
    def _insert_entity(
        connection: sqlite3.Connection,
        publication_id: str,
        entity: WorkspaceEntity,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workspace_entities_v2(
                entity_id, stable_id, entity_type, project_id, acl_ref, generation_id,
                version, display_key, label, status, authority, effective_at,
                current, archived, content_sha256, entity_json, publication_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity.entity_id,
                entity.stable_id,
                entity.entity_type,
                entity.scope.project_id,
                entity.scope.acl_ref,
                entity.scope.generation_id,
                entity.version,
                entity.display_key,
                entity.label,
                entity.status,
                entity.authority,
                entity.effective_at,
                int(entity.current),
                int(entity.archived),
                entity.content_sha256,
                canonical_json_bytes(entity.model_dump(mode="json")).decode(),
                publication_id,
            ),
        )
        table = WORKSPACE_ENTITY_TABLES[entity.entity_type]
        connection.execute(
            f"""
            INSERT INTO {table}(
                entity_id, project_id, generation_id, status, authority, content_sha256
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entity.entity_id,
                entity.scope.project_id,
                entity.scope.generation_id,
                entity.status,
                entity.authority,
                entity.content_sha256,
            ),
        )

    @staticmethod
    def _insert_edge(
        connection: sqlite3.Connection,
        publication_id: str,
        edge: WorkspaceEdge,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workspace_edges_v2(
                edge_id, edge_type, source_id, target_id, project_id, generation_id,
                reviewed, current, content_sha256, edge_json, publication_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.edge_id,
                edge.edge_type,
                edge.source_id,
                edge.target_id,
                edge.scope.project_id,
                edge.scope.generation_id,
                int(edge.reviewed),
                int(edge.current),
                edge.content_sha256,
                canonical_json_bytes(edge.model_dump(mode="json")).decode(),
                publication_id,
            ),
        )

    @staticmethod
    def _insert_unit(
        connection: sqlite3.Connection,
        publication_id: str,
        unit: WorkspaceRetrievalUnit,
    ) -> None:
        connection.execute(
            """
            INSERT INTO workspace_retrieval_units_v2(
                unit_id, entity_id, project_id, acl_ref, generation_id,
                unit_type, content_sha256, unit_json, publication_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unit.unit_id,
                unit.entity_id,
                unit.scope.project_id,
                unit.scope.acl_ref,
                unit.scope.generation_id,
                unit.unit_type,
                unit.content_sha256,
                canonical_json_bytes(unit.model_dump(mode="json")).decode(),
                publication_id,
            ),
        )

    def tombstone(self, tombstone: WorkspaceTombstone) -> None:
        self.initialize()
        with self._connect() as connection:
            entity = connection.execute(
                """
                SELECT project_id, generation_id
                FROM workspace_entities_v2
                WHERE entity_id=?
                """,
                (tombstone.target_entity_id,),
            ).fetchone()
            if (
                not entity
                or entity["project_id"] != tombstone.scope.project_id
                or entity["generation_id"] != tombstone.scope.generation_id
            ):
                raise WorkspacePublicationError("tombstone target is outside exact scope")
            connection.execute(
                """
                INSERT INTO workspace_tombstones_v2(
                    tombstone_id, target_entity_id, project_id, generation_id,
                    content_sha256, tombstone_json, effective_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tombstone.tombstone_id,
                    tombstone.target_entity_id,
                    tombstone.scope.project_id,
                    tombstone.scope.generation_id,
                    tombstone.content_sha256,
                    canonical_json_bytes(tombstone.model_dump(mode="json")).decode(),
                    tombstone.effective_at,
                ),
            )
        self._assert_no_sidecars()

    def list_entities(
        self,
        scope: WorkspaceScope,
        *,
        entity_types: tuple[WorkspaceEntityType, ...] = (),
        include_history: bool = False,
        as_of: str | None = None,
        allowed_acl_refs: tuple[str, ...] = (),
        enforce_acl: bool = False,
    ) -> tuple[WorkspaceEntity, ...]:
        if enforce_acl and not scope.permits(allowed_acl_refs, enforce_acl=True):
            return ()
        clauses = [
            "e.project_id=?",
            "e.generation_id=?",
            "p.active=1",
            "t.target_entity_id IS NULL",
        ]
        parameters: list[object] = [scope.project_id, scope.generation_id]
        if not include_history:
            clauses.extend(["e.current=1", "e.archived=0"])
        if as_of:
            clauses.append("e.effective_at<=?")
            parameters.append(as_of)
        if entity_types:
            placeholders = ",".join("?" for _ in entity_types)
            clauses.append(f"e.entity_type IN ({placeholders})")
            parameters.extend(item.value for item in entity_types)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT e.entity_json
                FROM workspace_entities_v2 e
                JOIN workspace_publications_v2 p ON p.publication_id=e.publication_id
                LEFT JOIN workspace_tombstones_v2 t ON t.target_entity_id=e.entity_id
                WHERE {" AND ".join(clauses)}
                ORDER BY e.entity_type, e.display_key, e.entity_id
                """,
                parameters,
            ).fetchall()
        return tuple(WorkspaceEntity.model_validate(json.loads(row["entity_json"])) for row in rows)

    def list_units(
        self,
        scope: WorkspaceScope,
        *,
        allowed_acl_refs: tuple[str, ...] = (),
        enforce_acl: bool = False,
    ) -> tuple[WorkspaceRetrievalUnit, ...]:
        if enforce_acl and not scope.permits(allowed_acl_refs, enforce_acl=True):
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT u.unit_json
                FROM workspace_retrieval_units_v2 u
                JOIN workspace_publications_v2 p ON p.publication_id=u.publication_id
                JOIN workspace_entities_v2 e ON e.entity_id=u.entity_id
                LEFT JOIN workspace_tombstones_v2 t ON t.target_entity_id=u.entity_id
                WHERE u.project_id=? AND u.generation_id=? AND p.active=1
                  AND e.current=1 AND e.archived=0 AND t.target_entity_id IS NULL
                ORDER BY u.unit_id
                """,
                (scope.project_id, scope.generation_id),
            ).fetchall()
        return tuple(
            WorkspaceRetrievalUnit.model_validate(json.loads(row["unit_json"])) for row in rows
        )

    def list_edges(
        self,
        scope: WorkspaceScope,
        *,
        include_history: bool = False,
    ) -> tuple[WorkspaceEdge, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT e.edge_json
                FROM workspace_edges_v2 e
                JOIN workspace_publications_v2 p ON p.publication_id=e.publication_id
                WHERE e.project_id=? AND e.generation_id=? AND p.active=1
                  AND (? OR (e.current=1 AND e.reviewed=1))
                ORDER BY e.edge_id
                """,
                (scope.project_id, scope.generation_id, int(include_history)),
            ).fetchall()
        return tuple(WorkspaceEdge.model_validate(json.loads(row["edge_json"])) for row in rows)

    def active_publication(self, project_id: str) -> WorkspacePublication | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT publication_json
                FROM workspace_publications_v2
                WHERE project_id=? AND active=1
                """,
                (project_id,),
            ).fetchone()
        return (
            WorkspacePublication.model_validate(json.loads(row["publication_json"]))
            if row
            else None
        )

    def rollback(self, *, project_id: str, publication_id: str) -> None:
        with self._connect() as connection:
            target = connection.execute(
                """
                SELECT publication_id
                FROM workspace_publications_v2
                WHERE project_id=? AND publication_id=?
                """,
                (project_id, publication_id),
            ).fetchone()
            if not target:
                raise WorkspacePublicationError("rollback publication is unavailable")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE workspace_publications_v2 SET active=0 WHERE project_id=?",
                (project_id,),
            )
            connection.execute(
                "UPDATE workspace_publications_v2 SET active=1 WHERE publication_id=?",
                (publication_id,),
            )
        self._assert_no_sidecars()

    def table_count(self, table: str) -> int:
        allowed = {
            "workspace_publications_v2",
            "workspace_entities_v2",
            "workspace_edges_v2",
            "workspace_retrieval_units_v2",
            "workspace_tombstones_v2",
            *WORKSPACE_ENTITY_TABLES.values(),
        }
        if table not in allowed:
            raise WorkspaceStoreError("unsupported Workspace table")
        with self._connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def stat(self) -> os.stat_result:
        return self.path.stat()


__all__ = [
    "WorkspacePublicationError",
    "WorkspaceStoreError",
    "WorkspaceStorePathError",
    "WorkspaceV2Store",
]
