"""Explicit isolated SQLite owner for Scientific Document V2."""

from __future__ import annotations

import json
import re
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import (
    DocumentPublication,
    DocumentScope,
    DocumentTombstone,
    canonical_sha256,
)
from .schema import DOCUMENT_V2_ENTITY_TABLES, DOCUMENT_V2_SCHEMA, DOCUMENT_V2_TABLES

_SIDECAR_RE = re.compile(r"(?i)(?:-wal|-shm|\.wal|\.shm|\.db-journal)$")


class DocumentStoreError(RuntimeError):
    """Base isolated Document store error."""


class DocumentStorePathError(DocumentStoreError):
    """Raised when an isolated database path is unsafe."""


class DocumentPublicationError(DocumentStoreError):
    """Raised for identity/scope/publication conflicts."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _path_has_symlink(path: Path, stop: Path) -> bool:
    current = path
    while current != stop:
        if current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return stop.is_symlink()


class DocumentV2Store:
    """Own only a caller-supplied SQLite file below an explicit isolated root."""

    def __init__(self, database_path: Path, *, isolated_root: Path) -> None:
        if not database_path.is_absolute() or not isolated_root.is_absolute():
            raise DocumentStorePathError("database path and isolated root must be absolute")
        root = isolated_root.resolve(strict=False)
        database = database_path.resolve(strict=False)
        if database == root or not database.is_relative_to(root):
            raise DocumentStorePathError("database path must be below isolated root")
        if database.name == "evidence-rag.sqlite3" or _SIDECAR_RE.search(database.name):
            raise DocumentStorePathError("formal database and sidecar filenames are forbidden")
        if _path_has_symlink(database_path.parent, isolated_root):
            raise DocumentStorePathError("isolated database path must not traverse symlinks")
        if database_path.exists():
            mode = database_path.lstat().st_mode
            if not stat.S_ISREG(mode) or database_path.is_symlink():
                raise DocumentStorePathError("existing database target must be a regular file")
        self.database_path = database
        self.isolated_root = root

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if _path_has_symlink(self.database_path.parent, self.isolated_root):
            raise DocumentStorePathError("isolated database parent changed to a symlink")
        with self._connection() as db:
            db.executescript(DOCUMENT_V2_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = MEMORY")
        connection.execute("PRAGMA temp_store = MEMORY")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def publish(self, publication: DocumentPublication) -> dict[str, Any]:
        publication = DocumentPublication.model_validate(
            publication.model_dump(mode="python", round_trip=True)
        )
        payload = publication.model_dump(mode="json")
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        scope = publication.family.scope
        with self._transaction() as db:
            existing = db.execute(
                "SELECT publication_json FROM document_publications_v2 WHERE publication_id=?",
                (publication.publication_id,),
            ).fetchone()
            if existing:
                if existing["publication_json"] != serialized:
                    raise DocumentPublicationError("publication identity collision")
                return self._publication_result(db, publication.publication_id, "already_published")
            tombstone = db.execute(
                """SELECT tombstone_id FROM document_tombstones_v2
                   WHERE project_id=? AND acl_ref=? AND generation_id=? AND family_id=?
                     AND (version_id IS NULL OR version_id=?)""",
                (
                    scope.project_id,
                    scope.acl_ref,
                    scope.generation_id,
                    publication.family.family_id,
                    publication.version.version_id,
                ),
            ).fetchone()
            if tombstone:
                raise DocumentPublicationError(
                    "cannot publish a tombstoned document family/version"
                )
            family = publication.family
            db.execute(
                """INSERT INTO document_families_v2
                   (family_id, project_id, acl_ref, source_key, canonical_title, locator)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(family_id) DO NOTHING""",
                (
                    family.family_id,
                    scope.project_id,
                    scope.acl_ref,
                    family.source_key,
                    family.canonical_title,
                    family.locator,
                ),
            )
            stored_family = db.execute(
                "SELECT * FROM document_families_v2 WHERE family_id=?",
                (family.family_id,),
            ).fetchone()
            if (
                not stored_family
                or stored_family["project_id"] != scope.project_id
                or stored_family["acl_ref"] != scope.acl_ref
                or stored_family["source_key"] != family.source_key
            ):
                raise DocumentPublicationError("family identity conflicts with stored scope")
            version = publication.version
            db.execute(
                """INSERT INTO document_versions_v2
                   (version_id, family_id, project_id, acl_ref, generation_id,
                    version_label, source_kind, content_sha256, parse_quality,
                    parser_version, source_text, locator, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (
                    version.version_id,
                    version.family_id,
                    scope.project_id,
                    scope.acl_ref,
                    scope.generation_id,
                    version.version_label,
                    version.source_kind,
                    version.content_sha256,
                    version.parse_quality,
                    version.parser_version,
                    version.source_text,
                    version.locator,
                ),
            )
            for entity in publication.entities:
                entity_json = json.dumps(
                    entity.model_dump(mode="json"),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                db.execute(
                    """INSERT INTO document_entities_v2
                       (entity_id, stable_id, entity_type, family_id, version_id,
                        project_id, acl_ref, generation_id, parent_id, ordinal,
                        page_number, source_text, derived_text, content_sha256,
                        locator, authority, status, entity_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entity.entity_id,
                        entity.stable_id,
                        entity.entity_type,
                        entity.family_id,
                        entity.version_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        entity.parent_id,
                        entity.ordinal,
                        entity.page_number,
                        entity.source_text,
                        entity.derived_text,
                        entity.content_sha256,
                        entity.locator,
                        entity.authority,
                        entity.status,
                        entity_json,
                    ),
                )
                table_name = DOCUMENT_V2_ENTITY_TABLES[entity.entity_type.value]
                db.execute(
                    f"""INSERT INTO {table_name}
                        (entity_id, version_id, project_id, acl_ref, generation_id, typed_json)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        entity.entity_id,
                        entity.version_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        entity_json,
                    ),
                )
            db.executemany(
                """INSERT INTO document_retrieval_units_v2
                   (unit_id, version_id, project_id, acl_ref, generation_id, entity_id,
                    entity_type, exact_keys_json, sparse_text, dense_source_text,
                    dense_derived_text, source_text_sha256, derived_text_sha256,
                    builder_version, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        unit.unit_id,
                        unit.version_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        unit.entity_id,
                        unit.entity_type,
                        json.dumps(unit.exact_keys, ensure_ascii=False, separators=(",", ":")),
                        unit.sparse_text,
                        unit.dense_source_text,
                        unit.dense_derived_text,
                        unit.source_text_sha256,
                        unit.derived_text_sha256,
                        unit.builder_version,
                        unit.locator,
                    )
                    for unit in publication.retrieval_units
                ],
            )
            db.executemany(
                """INSERT INTO document_edges_v2
                   (edge_id, version_id, project_id, acl_ref, generation_id, source_id,
                    target_id, edge_type, authority, confidence, review_status, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        edge.edge_id,
                        edge.version_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        edge.source_id,
                        edge.target_id,
                        edge.edge_type,
                        edge.authority,
                        edge.confidence,
                        edge.review_status,
                        edge.locator,
                    )
                    for edge in publication.edges
                ],
            )
            db.execute(
                """INSERT INTO document_publications_v2
                   (publication_id, project_id, acl_ref, generation_id, family_id,
                    version_id, source_payload_sha256, publication_sha256,
                    publication_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    publication.publication_id,
                    scope.project_id,
                    scope.acl_ref,
                    scope.generation_id,
                    family.family_id,
                    version.version_id,
                    publication.source_payload_sha256,
                    publication.publication_sha256,
                    serialized,
                    _utc_now(),
                ),
            )
            db.execute(
                "UPDATE document_families_v2 SET latest_version_id=? WHERE family_id=?",
                (version.version_id, family.family_id),
            )
            return self._publication_result(db, publication.publication_id, "published")

    @staticmethod
    def _publication_result(
        db: sqlite3.Connection, publication_id: str, status: str
    ) -> dict[str, Any]:
        row = db.execute(
            """SELECT publication_id, family_id, version_id, publication_sha256,
                      generation_id FROM document_publications_v2 WHERE publication_id=?""",
            (publication_id,),
        ).fetchone()
        if not row:
            raise DocumentPublicationError("publication disappeared during transaction")
        return {**dict(row), "status": status}

    def list_publications(
        self,
        scope: DocumentScope,
        *,
        include_superseded: bool = True,
    ) -> tuple[DocumentPublication, ...]:
        with self._connection() as db:
            clauses = [
                "p.project_id=?",
                "p.acl_ref=?",
                "p.generation_id=?",
                """NOT EXISTS (
                    SELECT 1 FROM document_tombstones_v2 t
                    WHERE t.project_id=p.project_id AND t.acl_ref=p.acl_ref
                      AND t.generation_id=p.generation_id AND t.family_id=p.family_id
                      AND (t.version_id IS NULL OR t.version_id=p.version_id)
                )""",
            ]
            if not include_superseded:
                clauses.append(
                    """p.version_id=(SELECT latest_version_id FROM document_families_v2 f
                                      WHERE f.family_id=p.family_id)"""
                )
            rows = db.execute(
                f"""SELECT p.publication_json FROM document_publications_v2 p
                    WHERE {" AND ".join(clauses)}
                    ORDER BY p.family_id, p.version_id""",
                (scope.project_id, scope.acl_ref, scope.generation_id),
            ).fetchall()
        return tuple(
            DocumentPublication.model_validate_json(row["publication_json"]) for row in rows
        )

    def tombstone(
        self,
        scope: DocumentScope,
        *,
        family_id: str,
        version_id: str | None,
        reason: str,
    ) -> DocumentTombstone:
        payload = {
            "scope": scope.model_dump(mode="json"),
            "family_id": family_id,
            "version_id": version_id,
            "reason": reason,
        }
        tombstone_id = "doctomb-" + canonical_sha256(payload).removeprefix("sha256:")[:32]
        item = DocumentTombstone(
            tombstone_id=tombstone_id,
            scope=scope,
            family_id=family_id,
            version_id=version_id,
            reason=reason,
            content_sha256=canonical_sha256(payload),
        )
        with self._transaction() as db:
            if not db.execute(
                """SELECT 1 FROM document_families_v2
                   WHERE family_id=? AND project_id=? AND acl_ref=?""",
                (family_id, scope.project_id, scope.acl_ref),
            ).fetchone():
                raise DocumentPublicationError("tombstone family is outside governed scope")
            if (
                version_id
                and not db.execute(
                    """SELECT 1 FROM document_versions_v2
                   WHERE version_id=? AND family_id=? AND generation_id=?""",
                    (version_id, family_id, scope.generation_id),
                ).fetchone()
            ):
                raise DocumentPublicationError("tombstone version is outside governed scope")
            db.execute(
                """INSERT INTO document_tombstones_v2
                   (tombstone_id, project_id, acl_ref, generation_id, family_id,
                    version_id, reason, content_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tombstone_id) DO NOTHING""",
                (
                    item.tombstone_id,
                    scope.project_id,
                    scope.acl_ref,
                    scope.generation_id,
                    family_id,
                    version_id,
                    reason,
                    item.content_sha256,
                    _utc_now(),
                ),
            )
            if version_id:
                db.execute(
                    "UPDATE document_versions_v2 SET active=0 WHERE version_id=?",
                    (version_id,),
                )
        return item

    def table_counts(self) -> dict[str, int]:
        with self._connection() as db:
            return {
                table: int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in DOCUMENT_V2_TABLES
            }

    def database_sidecars(self) -> tuple[str, ...]:
        parent = self.database_path.parent
        prefix = self.database_path.name
        return tuple(
            sorted(
                item.name
                for item in parent.iterdir()
                if item.name.startswith(prefix) and item != self.database_path
            )
        )
