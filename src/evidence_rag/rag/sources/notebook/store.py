"""Explicit isolated SQLite owner for Notebook Source V2."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import NotebookPublication, NotebookScope, canonical_sha256
from .schema import NOTEBOOK_V2_SCHEMA, NOTEBOOK_V2_TABLES


class NotebookStoreError(RuntimeError):
    """Base isolated Notebook V2 store error."""


class NotebookStorePathError(NotebookStoreError):
    """Raised when the requested database path is not demonstrably isolated."""


class NotebookPublicationError(NotebookStoreError):
    """Raised when a publication conflicts with already published identity."""


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,511}$")


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


class NotebookV2Store:
    """Own only a caller-supplied SQLite file below an explicit isolated root."""

    def __init__(self, database_path: Path, *, isolated_root: Path) -> None:
        if not database_path.is_absolute() or not isolated_root.is_absolute():
            raise NotebookStorePathError("database path and isolated root must be absolute")
        root = isolated_root.resolve(strict=False)
        database = database_path.resolve(strict=False)
        if database == root or not database.is_relative_to(root):
            raise NotebookStorePathError("database path must be below isolated root")
        if database.name == "evidence-rag.sqlite3":
            raise NotebookStorePathError("formal database filename is forbidden")
        if _path_has_symlink(database_path.parent, isolated_root):
            raise NotebookStorePathError("isolated database path must not traverse symlinks")
        if database_path.exists() and (database_path.is_symlink() or not database_path.is_file()):
            raise NotebookStorePathError("existing database target must be a regular file")
        self.database_path = database
        self.isolated_root = root

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if _path_has_symlink(self.database_path.parent, self.isolated_root):
            raise NotebookStorePathError("isolated database parent changed to a symlink")
        with self._connection() as db:
            db.executescript(NOTEBOOK_V2_SCHEMA)

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

    def publish(self, publication: NotebookPublication) -> dict[str, Any]:
        publication = NotebookPublication.model_validate(
            publication.model_dump(mode="python", round_trip=True)
        )
        scope = publication.template.scope
        payload = publication.model_dump(mode="json")
        canonical_json = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._transaction() as db:
            existing = db.execute(
                "SELECT publication_json FROM notebook_publications_v2 WHERE publication_id=?",
                (publication.publication_id,),
            ).fetchone()
            if existing:
                if existing["publication_json"] != canonical_json:
                    raise NotebookPublicationError("publication identity collision")
                return self._publication_result(db, publication.publication_id, "already_published")
            tombstone = db.execute(
                """SELECT tombstone_id FROM notebook_tombstones_v2
                   WHERE project_id=? AND generation_id=? AND template_id=?""",
                (
                    scope.project_id,
                    scope.generation_id,
                    publication.template.template_id,
                ),
            ).fetchone()
            if tombstone:
                raise NotebookPublicationError(
                    "cannot publish into a tombstoned Notebook generation"
                )

            template = publication.template
            db.execute(
                """INSERT INTO notebook_templates_v2
                   (template_id, project_id, acl_ref, generation_id, source_key, name,
                    language, kernel_name, locator, active_revision_id, active_execution_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                   ON CONFLICT(template_id) DO NOTHING""",
                (
                    template.template_id,
                    scope.project_id,
                    scope.acl_ref,
                    scope.generation_id,
                    template.source_key,
                    template.name,
                    template.language,
                    template.kernel_name,
                    template.locator,
                ),
            )
            stored_template = db.execute(
                "SELECT * FROM notebook_templates_v2 WHERE template_id=?",
                (template.template_id,),
            ).fetchone()
            if (
                not stored_template
                or stored_template["project_id"] != scope.project_id
                or stored_template["acl_ref"] != scope.acl_ref
                or stored_template["source_key"] != template.source_key
            ):
                raise NotebookPublicationError("template identity conflicts with stored scope")

            revision = publication.revision
            db.execute(
                """INSERT INTO notebook_revisions
                   (revision_id, template_id, project_id, acl_ref, generation_id,
                    source_version, content_sha256, metadata_sha256, nbformat,
                    nbformat_minor, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(revision_id) DO NOTHING""",
                (
                    revision.revision_id,
                    revision.template_id,
                    scope.project_id,
                    scope.acl_ref,
                    scope.generation_id,
                    revision.source_version,
                    revision.content_sha256,
                    revision.metadata_sha256,
                    revision.nbformat,
                    revision.nbformat_minor,
                    revision.locator,
                ),
            )
            stored_revision = db.execute(
                "SELECT * FROM notebook_revisions WHERE revision_id=?",
                (revision.revision_id,),
            ).fetchone()
            if (
                not stored_revision
                or stored_revision["template_id"] != revision.template_id
                or stored_revision["content_sha256"] != revision.content_sha256
                or stored_revision["acl_ref"] != scope.acl_ref
            ):
                raise NotebookPublicationError("revision identity conflicts with stored content")
            execution = publication.execution
            db.execute(
                """INSERT INTO notebook_executions
                   (execution_id, template_id, revision_id, project_id, acl_ref,
                    generation_id, execution_key, status, started_at, completed_at,
                    content_sha256, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution.execution_id,
                    execution.template_id,
                    execution.revision_id,
                    scope.project_id,
                    scope.acl_ref,
                    scope.generation_id,
                    execution.execution_key,
                    execution.status,
                    execution.started_at,
                    execution.completed_at,
                    execution.content_sha256,
                    execution.locator,
                ),
            )
            db.executemany(
                """INSERT INTO notebook_cell_versions
                   (cell_version_id, stable_cell_id, revision_id, project_id, acl_ref,
                    generation_id, display_order, cell_type, source, source_sha256,
                    tags_json, native_cell_id, identity_confidence, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cell_version_id) DO NOTHING""",
                [
                    (
                        item.cell_version_id,
                        item.stable_cell_id,
                        item.revision_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        item.display_order,
                        item.cell_type,
                        item.source,
                        item.source_sha256,
                        json.dumps(item.tags, separators=(",", ":")),
                        item.native_cell_id,
                        item.identity_confidence,
                        item.locator,
                    )
                    for item in publication.cell_versions
                ],
            )
            for item in publication.cell_versions:
                stored_cell = db.execute(
                    """SELECT revision_id, source_sha256, project_id, acl_ref
                       FROM notebook_cell_versions WHERE cell_version_id=?""",
                    (item.cell_version_id,),
                ).fetchone()
                if (
                    not stored_cell
                    or stored_cell["revision_id"] != item.revision_id
                    or stored_cell["source_sha256"] != item.source_sha256
                    or stored_cell["project_id"] != scope.project_id
                    or stored_cell["acl_ref"] != scope.acl_ref
                ):
                    raise NotebookPublicationError(
                        "cell version identity conflicts with stored content"
                    )
            db.executemany(
                """INSERT INTO notebook_cell_executions
                   (cell_execution_id, execution_id, cell_version_id, project_id, acl_ref,
                    generation_id, display_order, execution_count, execution_order, state,
                    stale, diagnostics_json, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.cell_execution_id,
                        item.execution_id,
                        item.cell_version_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        item.display_order,
                        item.execution_count,
                        item.execution_order,
                        item.state,
                        int(item.stale),
                        json.dumps(item.diagnostics, separators=(",", ":")),
                        item.locator,
                    )
                    for item in publication.cell_executions
                ],
            )
            db.executemany(
                """INSERT INTO notebook_parameters
                   (parameter_id, execution_id, cell_version_id, project_id, acl_ref,
                    generation_id, name, value_type, canonical_value, value_sha256, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.parameter_id,
                        item.execution_id,
                        item.cell_version_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        item.name,
                        item.value_type,
                        item.canonical_value,
                        item.value_sha256,
                        item.locator,
                    )
                    for item in publication.parameters
                ],
            )
            db.executemany(
                """INSERT INTO notebook_symbols
                   (symbol_id, cell_version_id, revision_id, project_id, acl_ref,
                    generation_id, name, role, confidence, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(symbol_id) DO NOTHING""",
                [
                    (
                        item.symbol_id,
                        item.cell_version_id,
                        item.revision_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        item.name,
                        item.role,
                        item.confidence,
                        item.locator,
                    )
                    for item in publication.symbols
                ],
            )
            for item in publication.symbols:
                stored_symbol = db.execute(
                    """SELECT cell_version_id, revision_id, name, role, project_id, acl_ref
                       FROM notebook_symbols WHERE symbol_id=?""",
                    (item.symbol_id,),
                ).fetchone()
                if (
                    not stored_symbol
                    or stored_symbol["cell_version_id"] != item.cell_version_id
                    or stored_symbol["revision_id"] != item.revision_id
                    or stored_symbol["name"] != item.name
                    or stored_symbol["role"] != item.role
                    or stored_symbol["project_id"] != scope.project_id
                    or stored_symbol["acl_ref"] != scope.acl_ref
                ):
                    raise NotebookPublicationError("symbol identity conflicts with stored content")
            db.executemany(
                """INSERT INTO notebook_artifacts
                   (artifact_id, execution_id, cell_execution_id, project_id, acl_ref,
                   generation_id, ordinal, artifact_type, mime_types_json, text_content,
                    error_name, error_value, binary_omitted, metric_confirmed,
                    content_sha256, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.artifact_id,
                        item.execution_id,
                        item.cell_execution_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        item.ordinal,
                        item.artifact_type,
                        json.dumps(item.mime_types, separators=(",", ":")),
                        item.text,
                        item.error_name,
                        item.error_value,
                        int(item.binary_omitted),
                        int(item.metric_confirmed),
                        item.content_sha256,
                        item.locator,
                    )
                    for item in publication.artifacts
                ],
            )
            db.executemany(
                """INSERT INTO notebook_retrieval_units
                   (unit_id, revision_id, execution_id, project_id, acl_ref, generation_id,
                    entity_id, unit_type, profile, content, content_sha256, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(unit_id) DO NOTHING""",
                [
                    (
                        item.unit_id,
                        item.revision_id,
                        item.execution_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        item.entity_id,
                        item.unit_type,
                        item.profile,
                        item.content,
                        item.content_sha256,
                        item.locator,
                    )
                    for item in publication.retrieval_units
                ],
            )
            for item in publication.retrieval_units:
                stored_unit = db.execute(
                    """SELECT revision_id, execution_id, entity_id, content_sha256,
                              project_id, acl_ref
                       FROM notebook_retrieval_units WHERE unit_id=?""",
                    (item.unit_id,),
                ).fetchone()
                if (
                    not stored_unit
                    or stored_unit["revision_id"] != item.revision_id
                    or stored_unit["execution_id"] != item.execution_id
                    or stored_unit["entity_id"] != item.entity_id
                    or stored_unit["content_sha256"] != item.content_sha256
                    or stored_unit["project_id"] != scope.project_id
                    or stored_unit["acl_ref"] != scope.acl_ref
                ):
                    raise NotebookPublicationError(
                        "retrieval unit identity conflicts with stored content"
                    )
            db.executemany(
                """INSERT INTO notebook_edges
                   (edge_id, revision_id, execution_id, project_id, acl_ref, generation_id,
                    source_id, target_id, edge_type, confidence, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(edge_id) DO NOTHING""",
                [
                    (
                        item.edge_id,
                        item.revision_id,
                        item.execution_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        item.source_id,
                        item.target_id,
                        item.edge_type,
                        item.confidence,
                        item.locator,
                    )
                    for item in publication.edges
                ],
            )
            for item in publication.edges:
                stored_edge = db.execute(
                    """SELECT revision_id, execution_id, source_id, target_id, edge_type,
                              project_id, acl_ref
                       FROM notebook_edges WHERE edge_id=?""",
                    (item.edge_id,),
                ).fetchone()
                if (
                    not stored_edge
                    or stored_edge["revision_id"] != item.revision_id
                    or stored_edge["execution_id"] != item.execution_id
                    or stored_edge["source_id"] != item.source_id
                    or stored_edge["target_id"] != item.target_id
                    or stored_edge["edge_type"] != item.edge_type
                    or stored_edge["project_id"] != scope.project_id
                    or stored_edge["acl_ref"] != scope.acl_ref
                ):
                    raise NotebookPublicationError("edge identity conflicts with stored graph")
            db.executemany(
                """INSERT INTO notebook_comparisons_v2
                   (comparison_id, project_id, acl_ref, generation_id,
                    baseline_revision_id, candidate_revision_id, matches_sha256, locator)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.comparison_id,
                        scope.project_id,
                        scope.acl_ref,
                        scope.generation_id,
                        item.baseline_revision_id,
                        item.candidate_revision_id,
                        item.matches_sha256,
                        item.locator,
                    )
                    for item in publication.comparisons
                ],
            )
            db.execute(
                """INSERT INTO notebook_publications_v2
                   (publication_id, project_id, acl_ref, generation_id,
                    source_payload_sha256, template_id, revision_id, execution_id,
                    publication_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    publication.publication_id,
                    scope.project_id,
                    scope.acl_ref,
                    scope.generation_id,
                    publication.source_payload_sha256,
                    template.template_id,
                    revision.revision_id,
                    execution.execution_id,
                    canonical_json,
                    _utc_now(),
                ),
            )
            db.execute(
                """UPDATE notebook_templates_v2
                   SET generation_id=?, active_revision_id=?, active_execution_id=?
                   WHERE template_id=?""",
                (
                    scope.generation_id,
                    revision.revision_id,
                    execution.execution_id,
                    template.template_id,
                ),
            )
            return self._publication_result(db, publication.publication_id, "published")

    def tombstone_template(
        self,
        *,
        project_id: str,
        acl_ref: str,
        generation_id: str,
        template_id: str,
        source_key: str,
        reason: str,
    ) -> dict[str, Any]:
        scope = NotebookScope(
            project_id=project_id,
            acl_ref=acl_ref,
            generation_id=generation_id,
        )
        if not _IDENTIFIER_RE.fullmatch(template_id):
            raise NotebookPublicationError("tombstone template id is not canonical")
        if not _IDENTIFIER_RE.fullmatch(source_key):
            raise NotebookPublicationError("tombstone source key is not canonical")
        if not _IDENTIFIER_RE.fullmatch(reason):
            raise NotebookPublicationError("tombstone reason is not canonical")
        payload = {
            "contract": "notebook-tombstone-v2",
            "scope": scope.model_dump(mode="json"),
            "template_id": template_id,
            "source_key": source_key,
            "reason": reason,
        }
        tombstone_sha256 = canonical_sha256(payload)
        tombstone_id = "nbtomb-" + tombstone_sha256.removeprefix("sha256:")
        with self._transaction() as db:
            template = db.execute(
                """SELECT project_id, acl_ref, source_key FROM notebook_templates_v2
                   WHERE template_id=?""",
                (template_id,),
            ).fetchone()
            if (
                not template
                or template["project_id"] != project_id
                or template["acl_ref"] != acl_ref
                or template["source_key"] != source_key
            ):
                raise NotebookPublicationError("tombstone authority does not match stored template")
            publication = db.execute(
                """SELECT 1 FROM notebook_publications_v2
                   WHERE project_id=? AND acl_ref=? AND generation_id=? AND template_id=?
                   LIMIT 1""",
                (project_id, acl_ref, generation_id, template_id),
            ).fetchone()
            if not publication:
                raise NotebookPublicationError("tombstone requires an exact published generation")
            db.execute(
                """INSERT INTO notebook_tombstones_v2
                   (tombstone_id, project_id, acl_ref, generation_id, template_id,
                    source_key, reason, tombstone_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id, generation_id, template_id) DO NOTHING""",
                (
                    tombstone_id,
                    project_id,
                    acl_ref,
                    generation_id,
                    template_id,
                    source_key,
                    reason,
                    tombstone_sha256,
                    _utc_now(),
                ),
            )
            stored = db.execute(
                """SELECT tombstone_id, source_key, reason, tombstone_sha256
                   FROM notebook_tombstones_v2
                   WHERE project_id=? AND generation_id=? AND template_id=?""",
                (project_id, generation_id, template_id),
            ).fetchone()
            if (
                not stored
                or stored["tombstone_id"] != tombstone_id
                or stored["source_key"] != source_key
                or stored["reason"] != reason
                or stored["tombstone_sha256"] != tombstone_sha256
            ):
                raise NotebookPublicationError("tombstone identity conflicts with stored state")
            db.execute(
                """UPDATE notebook_templates_v2
                   SET active_revision_id=NULL, active_execution_id=NULL
                   WHERE template_id=? AND generation_id=?""",
                (template_id, generation_id),
            )
        return {
            "tombstone_id": tombstone_id,
            "tombstone_sha256": tombstone_sha256,
            "disposition": "tombstoned",
            "database_sidecars": self._sidecars_present(),
        }

    def _publication_result(
        self,
        db: sqlite3.Connection,
        publication_id: str,
        disposition: str,
    ) -> dict[str, Any]:
        counts = {
            table: int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in NOTEBOOK_V2_TABLES
        }
        return {
            "publication_id": publication_id,
            "disposition": disposition,
            "counts": counts,
            "database_sidecars": self._sidecars_present(),
        }

    def get_publication(self, publication_id: str) -> NotebookPublication | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT publication_json FROM notebook_publications_v2 WHERE publication_id=?",
                (publication_id,),
            ).fetchone()
        if not row:
            return None
        return NotebookPublication.model_validate(json.loads(row["publication_json"]))

    def list_publications(
        self,
        *,
        project_id: str,
        allowed_acl_refs: tuple[str, ...],
        generation_id: str | None = None,
    ) -> tuple[NotebookPublication, ...]:
        if not allowed_acl_refs:
            return ()
        marks = ",".join("?" for _ in allowed_acl_refs)
        parameters: list[str] = [project_id, *allowed_acl_refs]
        generation_clause = ""
        if generation_id is not None:
            generation_clause = " AND p.generation_id=?"
            parameters.append(generation_id)
        with self._connection() as db:
            rows = db.execute(
                f"""SELECT p.publication_json FROM notebook_publications_v2 AS p
                    WHERE p.project_id=? AND p.acl_ref IN ({marks}){generation_clause}
                      AND NOT EXISTS (
                        SELECT 1 FROM notebook_tombstones_v2 AS t
                        WHERE t.project_id=p.project_id
                          AND t.generation_id=p.generation_id
                          AND t.template_id=p.template_id
                      )
                    ORDER BY p.revision_id, p.execution_id, p.publication_id""",
                parameters,
            ).fetchall()
        return tuple(
            NotebookPublication.model_validate(json.loads(row["publication_json"])) for row in rows
        )

    def table_counts(self) -> dict[str, int]:
        with self._connection() as db:
            return {
                table: int(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                for table in NOTEBOOK_V2_TABLES
            }

    def _sidecars_present(self) -> tuple[str, ...]:
        candidates = (
            Path(str(self.database_path) + "-wal"),
            Path(str(self.database_path) + "-shm"),
            Path(str(self.database_path) + "-journal"),
        )
        return tuple(path.name for path in candidates if path.exists())

    def database_mode(self) -> int:
        return os.stat(self.database_path).st_mode


__all__ = [
    "NotebookPublicationError",
    "NotebookStoreError",
    "NotebookStorePathError",
    "NotebookV2Store",
]
