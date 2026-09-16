"""Isolated additive SQLite owner for Codex episode and retrieval projections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .contracts import canonical_sha256
from .episode_v2 import CodexEpisodeBuildResult
from .retrieval_v2 import CodexCalibrationArtifactV2, CodexDenseCacheEntryV2
from .units_v2 import CodexRetrievalPublicationV2

CODEX_V2_SCHEMA_VERSION = "codex-derived-sqlite-schema-v2"

CODEX_V2_SCHEMA = """
CREATE TABLE IF NOT EXISTS codex_generations_v2 (
    generation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'rolled_back', 'tombstoned')),
    active INTEGER NOT NULL CHECK(active IN (0, 1))
);
CREATE TABLE IF NOT EXISTS codex_publications_v2 (
    publication_id TEXT PRIMARY KEY,
    publication_sha256 TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    publication_json TEXT NOT NULL
    ,FOREIGN KEY(generation_id) REFERENCES codex_generations_v2(generation_id)
);
CREATE TABLE IF NOT EXISTS codex_pipeline_bundles_v2 (
    bundle_key TEXT PRIMARY KEY,
    bundle_sha256 TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    authority_watermark TEXT NOT NULL,
    source_snapshot_sha256 TEXT NOT NULL,
    component_set_sha256 TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    payload_json TEXT NOT NULL,
    UNIQUE(
        project_id, source_id, generation_id, thread_id, acl_ref,
        authority_watermark, source_snapshot_sha256, component_set_sha256
    ),
    FOREIGN KEY(publication_id) REFERENCES codex_publications_v2(publication_id),
    FOREIGN KEY(generation_id) REFERENCES codex_generations_v2(generation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS codex_one_active_generation_v2
ON codex_generations_v2(project_id, source_id) WHERE active = 1;

CREATE TABLE IF NOT EXISTS codex_episode_versions_v2 (
    episode_version_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_sha256 TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    payload_json TEXT NOT NULL,
    FOREIGN KEY(publication_id) REFERENCES codex_publications_v2(publication_id),
    FOREIGN KEY(generation_id) REFERENCES codex_generations_v2(generation_id)
);
CREATE TABLE IF NOT EXISTS codex_episode_members_v2 (
    episode_version_id TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    source_item_sha256 TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(episode_version_id, source_item_id),
    FOREIGN KEY(episode_version_id)
      REFERENCES codex_episode_versions_v2(episode_version_id)
);
CREATE TABLE IF NOT EXISTS codex_retrieval_units_v2 (
    unit_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL,
    episode_version_id TEXT,
    role TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    evidence_level TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    payload_json TEXT NOT NULL,
    FOREIGN KEY(publication_id) REFERENCES codex_publications_v2(publication_id),
    FOREIGN KEY(generation_id) REFERENCES codex_generations_v2(generation_id)
);
CREATE TABLE IF NOT EXISTS codex_temporal_edges_v2 (
    edge_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    from_unit_id TEXT NOT NULL,
    to_unit_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    payload_json TEXT NOT NULL,
    FOREIGN KEY(publication_id) REFERENCES codex_publications_v2(publication_id),
    FOREIGN KEY(from_unit_id) REFERENCES codex_retrieval_units_v2(unit_id),
    FOREIGN KEY(to_unit_id) REFERENCES codex_retrieval_units_v2(unit_id)
);
CREATE TABLE IF NOT EXISTS codex_tombstones_v2 (
    tombstone_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    source_cursor_id TEXT,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS codex_dense_cache_v2 (
    cache_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    profile_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    vector_sha256 TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    payload_json TEXT NOT NULL,
    UNIQUE(project_id, source_id, generation_id, acl_ref, unit_id,
           profile_sha256, content_sha256),
    FOREIGN KEY(generation_id) REFERENCES codex_generations_v2(generation_id)
);
CREATE TABLE IF NOT EXISTS codex_calibration_v2 (
    artifact_sha256 TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS codex_append_cursors_v2 (
    cursor_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    previous_cursor_id TEXT,
    disposition TEXT NOT NULL,
    cursor_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS codex_cursor_heads_v2 (
    project_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    cursor_id TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    PRIMARY KEY(project_id, source_id),
    FOREIGN KEY(cursor_id) REFERENCES codex_append_cursors_v2(cursor_id)
);
CREATE TABLE IF NOT EXISTS codex_release_records_v2 (
    evidence_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    decision_json TEXT NOT NULL
);
"""


class CodexV2StoreError(RuntimeError):
    """Base error for the isolated Codex V2 store."""


class CodexV2StorePathError(CodexV2StoreError):
    """The requested path is not demonstrably isolated."""


class CodexV2PublicationError(CodexV2StoreError):
    """A publication conflicts with stored immutable identity."""


def _path_has_symlink(path: Path, stop: Path) -> bool:
    current = path
    while current != stop:
        if current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return stop.is_symlink()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class CodexV2Store:
    """Publish only to a caller-supplied SQLite file under an isolated root."""

    def __init__(self, database_path: Path, *, isolated_root: Path) -> None:
        if not database_path.is_absolute() or not isolated_root.is_absolute():
            raise CodexV2StorePathError("database path and isolated root must be absolute")
        lexical_root = isolated_root.absolute()
        lexical_database = database_path.absolute()
        if lexical_database == lexical_root or not lexical_database.is_relative_to(lexical_root):
            raise CodexV2StorePathError("database path must be below isolated root")
        if database_path.name == "evidence-rag.sqlite3":
            raise CodexV2StorePathError("formal database filename is forbidden")
        if _path_has_symlink(database_path.parent, isolated_root):
            raise CodexV2StorePathError("isolated database path must not traverse symlinks")
        if database_path.exists() and (database_path.is_symlink() or not database_path.is_file()):
            raise CodexV2StorePathError("existing database target must be a regular file")
        self.database_path = database_path.resolve(strict=False)
        self.isolated_root = isolated_root.resolve(strict=False)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if _path_has_symlink(self.database_path.parent, self.isolated_root):
            raise CodexV2StorePathError("isolated database parent changed to a symlink")
        with self._connection() as db:
            db.executescript(CODEX_V2_SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = MEMORY")
        db.execute("PRAGMA temp_store = MEMORY")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def publish(
        self,
        episodes: CodexEpisodeBuildResult,
        publication: CodexRetrievalPublicationV2,
    ) -> dict[str, Any]:
        exact_episodes, exact_publication = self._validate_publication(episodes, publication)
        with self._transaction() as db:
            return self._publish_locked(db, exact_episodes, exact_publication)

    def publish_pipeline_bundle(
        self,
        bundle: object,
        *,
        authority_watermark: str,
        source_snapshot_sha256: str,
    ) -> dict[str, Any]:
        """Atomically publish and cache one source-authenticated pipeline bundle."""

        from .pipeline_v2 import CodexThreadPipelineBundleV2

        exact = CodexThreadPipelineBundleV2.model_validate(
            bundle.model_dump(mode="python", round_trip=True)  # type: ignore[attr-defined]
        )
        if not authority_watermark.startswith("sha256:") or not source_snapshot_sha256.startswith(
            "sha256:"
        ):
            raise CodexV2PublicationError("bundle authority digests are not canonical")
        bundle_key = self._bundle_key(
            project_id=exact.publication.project_id,
            source_id=exact.publication.source_id,
            generation_id=exact.publication.generation_id,
            thread_id=exact.publication.thread_id,
            acl_ref=exact.publication.acl_ref,
            authority_watermark=authority_watermark,
            source_snapshot_sha256=source_snapshot_sha256,
            component_set_sha256=exact.component_set_sha256,
        )
        payload = _canonical_json(exact.model_dump(mode="json"))
        exact_episodes, exact_publication = self._validate_publication(
            exact.episodes,
            exact.publication,
        )
        with self._transaction() as db:
            summary = self._publish_locked(db, exact_episodes, exact_publication)
            existing = db.execute(
                """SELECT bundle_sha256, publication_id, payload_json
                   FROM codex_pipeline_bundles_v2 WHERE bundle_key=?""",
                (bundle_key,),
            ).fetchone()
            if existing:
                if (
                    existing["bundle_sha256"] != exact.bundle_sha256
                    or existing["publication_id"] != summary["publication_id"]
                    or existing["payload_json"] != payload
                ):
                    raise CodexV2PublicationError("pipeline bundle identity collision")
            else:
                db.execute(
                    """INSERT INTO codex_pipeline_bundles_v2
                       (bundle_key, bundle_sha256, publication_id, project_id, source_id,
                        generation_id, thread_id, acl_ref, authority_watermark,
                        source_snapshot_sha256, component_set_sha256, active, payload_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                    (
                        bundle_key,
                        exact.bundle_sha256,
                        summary["publication_id"],
                        exact.publication.project_id,
                        exact.publication.source_id,
                        exact.publication.generation_id,
                        exact.publication.thread_id,
                        exact.publication.acl_ref,
                        authority_watermark,
                        source_snapshot_sha256,
                        exact.component_set_sha256,
                        payload,
                    ),
                )
            return {
                **summary,
                "bundle_key": bundle_key,
                "bundle_sha256": exact.bundle_sha256,
            }

    def lookup_pipeline_bundle(
        self,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        thread_id: str,
        acl_ref: str,
        authority_watermark: str,
        source_snapshot_sha256: str,
        component_set_sha256: str,
    ) -> object | None:
        from .pipeline_v2 import CodexThreadPipelineBundleV2

        bundle_key = self._bundle_key(
            project_id=project_id,
            source_id=source_id,
            generation_id=generation_id,
            thread_id=thread_id,
            acl_ref=acl_ref,
            authority_watermark=authority_watermark,
            source_snapshot_sha256=source_snapshot_sha256,
            component_set_sha256=component_set_sha256,
        )
        with self._connection() as db:
            row = db.execute(
                """SELECT b.payload_json
                   FROM codex_pipeline_bundles_v2 AS b
                   JOIN codex_generations_v2 AS g
                     ON g.generation_id=b.generation_id
                   LEFT JOIN codex_tombstones_v2 AS t
                     ON t.project_id=b.project_id AND t.source_id=b.source_id
                    AND t.generation_id=b.generation_id
                   WHERE b.bundle_key=? AND b.active=1
                     AND g.status='active' AND g.active=1
                     AND t.tombstone_id IS NULL""",
                (bundle_key,),
            ).fetchone()
        if row is None:
            return None
        return CodexThreadPipelineBundleV2.model_validate(json.loads(row["payload_json"]))

    @staticmethod
    def _bundle_key(
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        thread_id: str,
        acl_ref: str,
        authority_watermark: str,
        source_snapshot_sha256: str,
        component_set_sha256: str,
    ) -> str:
        return "codexbundle-" + canonical_sha256(
            {
                "project_id": project_id,
                "source_id": source_id,
                "generation_id": generation_id,
                "thread_id": thread_id,
                "acl_ref": acl_ref,
                "authority_watermark": authority_watermark,
                "source_snapshot_sha256": source_snapshot_sha256,
                "component_set_sha256": component_set_sha256,
            }
        ).removeprefix("sha256:")

    @staticmethod
    def _validate_publication(
        episodes: CodexEpisodeBuildResult,
        publication: CodexRetrievalPublicationV2,
    ) -> tuple[CodexEpisodeBuildResult, CodexRetrievalPublicationV2]:
        exact_episodes = CodexEpisodeBuildResult.model_validate(
            episodes.model_dump(mode="python", round_trip=True)
        )
        exact_publication = CodexRetrievalPublicationV2.model_validate(
            publication.model_dump(mode="python", round_trip=True)
        )
        if exact_publication.episode_result_sha256 != exact_episodes.result_sha256:
            raise CodexV2PublicationError("publication does not bind the episode result")
        if exact_publication.source_set_sha256 != exact_episodes.source_set_sha256:
            raise CodexV2PublicationError("publication does not bind the source set")
        return exact_episodes, exact_publication

    def _publish_locked(
        self,
        db: sqlite3.Connection,
        episodes: CodexEpisodeBuildResult,
        publication: CodexRetrievalPublicationV2,
    ) -> dict[str, Any]:
        publication_id = "codexpub-" + publication.publication_sha256.removeprefix("sha256:")
        publication_json = _canonical_json(publication.model_dump(mode="json"))
        tombstone = db.execute(
            """SELECT 1 FROM codex_tombstones_v2
               WHERE project_id=? AND source_id=? AND generation_id=?""",
            (publication.project_id, publication.source_id, publication.generation_id),
        ).fetchone()
        if tombstone:
            raise CodexV2PublicationError("generation is privacy-tombstoned")
        existing = db.execute(
            """SELECT publication_sha256, publication_json
               FROM codex_publications_v2 WHERE publication_id=?""",
            (publication_id,),
        ).fetchone()
        if existing:
            if (
                existing["publication_sha256"] != publication.publication_sha256
                or existing["publication_json"] != publication_json
            ):
                raise CodexV2PublicationError("publication identity collision")
            summary = self._summary(db, publication_id, "already_published")
            if summary["status"] != "active" or not summary["active"]:
                raise CodexV2PublicationError("publication generation is not active")
            return summary

        generation = db.execute(
            """SELECT project_id, source_id, acl_ref, status, active
               FROM codex_generations_v2 WHERE generation_id=?""",
            (publication.generation_id,),
        ).fetchone()
        if generation:
            if (
                generation["project_id"] != publication.project_id
                or generation["source_id"] != publication.source_id
                or generation["acl_ref"] != publication.acl_ref
                or generation["status"] != "active"
                or generation["active"] != 1
            ):
                raise CodexV2PublicationError("generation scope is not active and exact")
        else:
            prior_rows = db.execute(
                """SELECT generation_id FROM codex_generations_v2
                   WHERE project_id=? AND source_id=? AND active=1""",
                (publication.project_id, publication.source_id),
            ).fetchall()
            for prior in prior_rows:
                self._set_generation_active_locked(db, str(prior["generation_id"]), False)
            if prior_rows:
                db.execute(
                    """UPDATE codex_cursor_heads_v2 SET active=0
                       WHERE project_id=? AND source_id=?""",
                    (publication.project_id, publication.source_id),
                )
            generation_sha256 = canonical_sha256(
                {
                    "project_id": publication.project_id,
                    "source_id": publication.source_id,
                    "generation_id": publication.generation_id,
                    "acl_ref": publication.acl_ref,
                }
            )
            db.execute(
                """INSERT INTO codex_generations_v2
                   (generation_id, project_id, source_id, acl_ref,
                    generation_sha256, status, active)
                   VALUES (?, ?, ?, ?, ?, 'active', 1)""",
                (
                    publication.generation_id,
                    publication.project_id,
                    publication.source_id,
                    publication.acl_ref,
                    generation_sha256,
                ),
            )
        db.execute(
            """INSERT INTO codex_publications_v2
               (publication_id, publication_sha256, generation_id, thread_id,
                publication_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                publication_id,
                publication.publication_sha256,
                publication.generation_id,
                publication.thread_id,
                publication_json,
            ),
        )
        episode_by_id = {item.episode_id: item for item in episodes.episodes}
        for episode in episodes.episodes:
            payload = _canonical_json(episode.model_dump(mode="json"))
            existing_episode = db.execute(
                """SELECT payload_json FROM codex_episode_versions_v2
                   WHERE episode_version_id=?""",
                (episode.episode_version_id,),
            ).fetchone()
            if existing_episode:
                if existing_episode["payload_json"] != payload:
                    raise CodexV2PublicationError("episode identity collision")
                continue
            db.execute(
                """INSERT INTO codex_episode_versions_v2
                   (episode_version_id, publication_id, episode_id, generation_id, project_id,
                    source_id, thread_id, acl_ref, version, content_sha256, active,
                    payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    episode.episode_version_id,
                    publication_id,
                    episode.episode_id,
                    episode.generation_id,
                    episode.project_id,
                    episode.source_id,
                    episode.thread_id,
                    episode.acl_ref,
                    episode.version,
                    episode.content_sha256,
                    payload,
                ),
            )
        for member in episodes.members:
            episode = episode_by_id[member.episode_id]
            payload = _canonical_json(member.model_dump(mode="json"))
            existing_member = db.execute(
                """SELECT payload_json FROM codex_episode_members_v2
                   WHERE episode_version_id=? AND source_item_id=?""",
                (episode.episode_version_id, member.source_item_id),
            ).fetchone()
            if existing_member:
                if existing_member["payload_json"] != payload:
                    raise CodexV2PublicationError("episode member identity collision")
                continue
            db.execute(
                """INSERT INTO codex_episode_members_v2
                   (episode_version_id, source_item_id, ordinal, source_item_sha256,
                    source_locator, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    episode.episode_version_id,
                    member.source_item_id,
                    member.ordinal,
                    member.source_item_sha256,
                    member.source_locator,
                    payload,
                ),
            )
        for unit in publication.units:
            payload = _canonical_json(unit.model_dump(mode="json"))
            existing_unit = db.execute(
                "SELECT payload_json FROM codex_retrieval_units_v2 WHERE unit_id=?",
                (unit.unit_id,),
            ).fetchone()
            if existing_unit:
                if existing_unit["payload_json"] != payload:
                    raise CodexV2PublicationError("retrieval unit identity collision")
                continue
            db.execute(
                """INSERT INTO codex_retrieval_units_v2
                   (unit_id, publication_id, episode_version_id, role, generation_id, project_id,
                    source_id, thread_id, acl_ref, evidence_level, content_sha256,
                    active, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    unit.unit_id,
                    publication_id,
                    unit.episode_version_id,
                    unit.role,
                    unit.generation_id,
                    unit.project_id,
                    unit.source_id,
                    unit.thread_id,
                    unit.acl_ref,
                    unit.evidence_level,
                    unit.content_sha256,
                    payload,
                ),
            )
        for edge in publication.edges:
            payload = _canonical_json(edge.model_dump(mode="json"))
            existing_edge = db.execute(
                "SELECT payload_json FROM codex_temporal_edges_v2 WHERE edge_id=?",
                (edge.edge_id,),
            ).fetchone()
            if existing_edge:
                if existing_edge["payload_json"] != payload:
                    raise CodexV2PublicationError("temporal edge identity collision")
                continue
            db.execute(
                """INSERT INTO codex_temporal_edges_v2
                   (edge_id, publication_id, edge_type, from_unit_id, to_unit_id, generation_id,
                    project_id, source_id, thread_id, acl_ref, content_sha256,
                    active, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    edge.edge_id,
                    publication_id,
                    edge.edge_type,
                    edge.from_unit_id,
                    edge.to_unit_id,
                    edge.generation_id,
                    edge.project_id,
                    edge.source_id,
                    edge.thread_id,
                    edge.acl_ref,
                    edge.content_sha256,
                    payload,
                ),
            )
        return self._summary(db, publication_id, "published")

    @staticmethod
    def _set_generation_active_locked(
        db: sqlite3.Connection,
        generation_id: str,
        active: bool,
    ) -> None:
        flag = 1 if active else 0
        db.execute(
            "UPDATE codex_generations_v2 SET active=? WHERE generation_id=?",
            (flag, generation_id),
        )
        for table in (
            "codex_pipeline_bundles_v2",
            "codex_episode_versions_v2",
            "codex_retrieval_units_v2",
            "codex_temporal_edges_v2",
            "codex_dense_cache_v2",
        ):
            db.execute(
                f"UPDATE {table} SET active=? WHERE generation_id=?",  # noqa: S608
                (flag, generation_id),
            )

    def rollback(
        self,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
    ) -> dict[str, Any]:
        with self._transaction() as db:
            row = db.execute(
                """SELECT status, active, acl_ref FROM codex_generations_v2
                   WHERE project_id=? AND source_id=? AND generation_id=?""",
                (project_id, source_id, generation_id),
            ).fetchone()
            if not row:
                raise CodexV2PublicationError("generation is not published")
            if row["status"] == "tombstoned":
                raise CodexV2PublicationError("tombstoned generation cannot be restored")
            was_active = bool(row["active"])
            db.execute(
                """UPDATE codex_generations_v2
                   SET active=0, status='rolled_back' WHERE generation_id=?""",
                (generation_id,),
            )
            self._set_generation_active_locked(db, generation_id, False)
            lkg_generation_id: str | None = None
            if was_active:
                lkg = db.execute(
                    """SELECT generation_id FROM codex_generations_v2
                       WHERE project_id=? AND source_id=? AND acl_ref=?
                         AND generation_id<>? AND status='active'
                       ORDER BY rowid DESC LIMIT 1""",
                    (project_id, source_id, row["acl_ref"], generation_id),
                ).fetchone()
                if lkg is not None:
                    lkg_generation_id = str(lkg["generation_id"])
                    self._set_generation_active_locked(db, lkg_generation_id, True)
            head = db.execute(
                """SELECT h.cursor_id, c.generation_id
                   FROM codex_cursor_heads_v2 AS h
                   JOIN codex_append_cursors_v2 AS c ON c.cursor_id=h.cursor_id
                   WHERE h.project_id=? AND h.source_id=?""",
                (project_id, source_id),
            ).fetchone()
            if head is not None and head["generation_id"] == generation_id:
                replacement = (
                    db.execute(
                        """SELECT cursor_id FROM codex_append_cursors_v2
                           WHERE project_id=? AND source_id=? AND generation_id=?
                             AND disposition<>'FULL_REINGEST_REQUIRED'
                           ORDER BY rowid DESC LIMIT 1""",
                        (project_id, source_id, lkg_generation_id),
                    ).fetchone()
                    if lkg_generation_id is not None
                    else None
                )
                if replacement is None:
                    db.execute(
                        """UPDATE codex_cursor_heads_v2 SET active=0
                           WHERE project_id=? AND source_id=?""",
                        (project_id, source_id),
                    )
                else:
                    db.execute(
                        """UPDATE codex_cursor_heads_v2 SET cursor_id=?, active=1
                           WHERE project_id=? AND source_id=?""",
                        (replacement["cursor_id"], project_id, source_id),
                    )
            publications = db.execute(
                """SELECT COUNT(*) AS value FROM codex_publications_v2
                   WHERE generation_id=?""",
                (generation_id,),
            ).fetchone()["value"]
            return {
                "operation": "rolled_back",
                "generation_id": generation_id,
                "status": "rolled_back",
                "active": False,
                "publication_count": publications,
                "lkg_generation_id": lkg_generation_id,
                "schema_version": CODEX_V2_SCHEMA_VERSION,
            }

    def tombstone(
        self,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        reason: str,
        acl_ref: str | None = None,
        source_cursor_id: str | None = None,
    ) -> dict[str, Any]:
        if not reason or reason != reason.strip() or len(reason) > 256:
            raise CodexV2PublicationError("tombstone reason must be bounded")
        with self._transaction() as db:
            generation = db.execute(
                """SELECT project_id, source_id, acl_ref FROM codex_generations_v2
                   WHERE generation_id=?""",
                (generation_id,),
            ).fetchone()
            if generation is not None and (
                generation["project_id"] != project_id
                or generation["source_id"] != source_id
                or (acl_ref is not None and generation["acl_ref"] != acl_ref)
            ):
                raise CodexV2PublicationError("tombstone generation scope is not exact")
            exact_acl_ref = (
                str(generation["acl_ref"]) if generation is not None else str(acl_ref or "")
            )
            if not exact_acl_ref:
                raise CodexV2PublicationError("tombstone ACL authority is required")
            if source_cursor_id is not None:
                cursor = db.execute(
                    """SELECT project_id, source_id, generation_id, acl_ref
                       FROM codex_append_cursors_v2 WHERE cursor_id=?""",
                    (source_cursor_id,),
                ).fetchone()
                if cursor is None or (
                    cursor["project_id"],
                    cursor["source_id"],
                    cursor["generation_id"],
                    cursor["acl_ref"],
                ) != (project_id, source_id, generation_id, exact_acl_ref):
                    raise CodexV2PublicationError("tombstone source cursor authority is not exact")
            tombstone_id = "codextomb-" + canonical_sha256(
                {
                    "project_id": project_id,
                    "source_id": source_id,
                    "generation_id": generation_id,
                    "acl_ref": exact_acl_ref,
                    "source_cursor_id": source_cursor_id,
                    "reason": reason,
                }
            ).removeprefix("sha256:")
            db.execute(
                """INSERT INTO codex_tombstones_v2
                   (tombstone_id, project_id, source_id, generation_id, acl_ref,
                    source_cursor_id, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(tombstone_id) DO NOTHING""",
                (
                    tombstone_id,
                    project_id,
                    source_id,
                    generation_id,
                    exact_acl_ref,
                    source_cursor_id,
                    reason,
                ),
            )
            db.execute(
                """UPDATE codex_generations_v2
                   SET active=0, status='tombstoned'
                   WHERE project_id=? AND source_id=? AND generation_id=?""",
                (project_id, source_id, generation_id),
            )
            db.execute(
                """UPDATE codex_cursor_heads_v2 SET active=0
                   WHERE project_id=? AND source_id=?
                     AND cursor_id IN (
                         SELECT cursor_id FROM codex_append_cursors_v2
                         WHERE project_id=? AND source_id=? AND generation_id=?
                     )""",
                (project_id, source_id, project_id, source_id, generation_id),
            )
            for table in (
                "codex_pipeline_bundles_v2",
                "codex_episode_versions_v2",
                "codex_retrieval_units_v2",
                "codex_temporal_edges_v2",
                "codex_dense_cache_v2",
            ):
                db.execute(
                    f"UPDATE {table} SET active=0 WHERE generation_id=?",  # noqa: S608
                    (generation_id,),
                )
            return {
                "status": "tombstoned",
                "tombstone_id": tombstone_id,
                "generation_id": generation_id,
            }

    def active_units(
        self,
        *,
        project_id: str,
        source_id: str,
        acl_ref: str,
    ) -> tuple[dict[str, Any], ...]:
        with self._connection() as db:
            rows = db.execute(
                """SELECT u.payload_json FROM codex_retrieval_units_v2 AS u
                   JOIN codex_generations_v2 AS g
                     ON g.generation_id=u.generation_id
                   LEFT JOIN codex_tombstones_v2 AS t
                     ON t.project_id=u.project_id AND t.source_id=u.source_id
                    AND t.generation_id=u.generation_id
                   WHERE u.project_id=? AND u.source_id=? AND u.acl_ref=?
                     AND u.active=1 AND g.active=1 AND g.status='active'
                     AND t.tombstone_id IS NULL
                   ORDER BY u.unit_id""",
                (project_id, source_id, acl_ref),
            ).fetchall()
        return tuple(json.loads(row["payload_json"]) for row in rows)

    def active_derived_counts(
        self,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        acl_ref: str,
    ) -> dict[str, int]:
        with self._connection() as db:
            counts = {
                "bundles": db.execute(
                    """SELECT COUNT(*) AS value FROM codex_pipeline_bundles_v2
                       WHERE project_id=? AND source_id=? AND generation_id=?
                         AND acl_ref=? AND active=1""",
                    (project_id, source_id, generation_id, acl_ref),
                ).fetchone()["value"],
                "episodes": db.execute(
                    """SELECT COUNT(*) AS value FROM codex_episode_versions_v2
                       WHERE project_id=? AND source_id=? AND generation_id=?
                         AND acl_ref=? AND active=1""",
                    (project_id, source_id, generation_id, acl_ref),
                ).fetchone()["value"],
                "units": db.execute(
                    """SELECT COUNT(*) AS value FROM codex_retrieval_units_v2
                       WHERE project_id=? AND source_id=? AND generation_id=?
                         AND acl_ref=? AND active=1""",
                    (project_id, source_id, generation_id, acl_ref),
                ).fetchone()["value"],
                "edges": db.execute(
                    """SELECT COUNT(*) AS value FROM codex_temporal_edges_v2
                       WHERE project_id=? AND source_id=? AND generation_id=?
                         AND acl_ref=? AND active=1""",
                    (project_id, source_id, generation_id, acl_ref),
                ).fetchone()["value"],
                "dense_cache": db.execute(
                    """SELECT COUNT(*) AS value FROM codex_dense_cache_v2
                       WHERE project_id=? AND source_id=? AND generation_id=?
                         AND acl_ref=? AND active=1""",
                    (project_id, source_id, generation_id, acl_ref),
                ).fetchone()["value"],
                "cursor_heads": db.execute(
                    """SELECT COUNT(*) AS value
                       FROM codex_cursor_heads_v2 AS h
                       JOIN codex_append_cursors_v2 AS c ON c.cursor_id=h.cursor_id
                       WHERE h.project_id=? AND h.source_id=? AND h.active=1
                         AND c.generation_id=? AND c.acl_ref=?""",
                    (project_id, source_id, generation_id, acl_ref),
                ).fetchone()["value"],
            }
        return {name: int(value) for name, value in counts.items()}

    def put_dense_cache(self, entry: CodexDenseCacheEntryV2) -> str:
        entry = CodexDenseCacheEntryV2.model_validate(
            entry.model_dump(mode="python", round_trip=True)
        )
        payload = _canonical_json(entry.model_dump(mode="json"))
        with self._transaction() as db:
            generation = db.execute(
                """SELECT project_id, source_id, acl_ref, status, active
                   FROM codex_generations_v2 WHERE generation_id=?""",
                (entry.generation_id,),
            ).fetchone()
            if (
                generation is None
                or generation["project_id"] != entry.project_id
                or generation["source_id"] != entry.source_id
                or generation["acl_ref"] != entry.acl_ref
                or generation["status"] != "active"
                or generation["active"] != 1
            ):
                raise CodexV2PublicationError(
                    "dense cache requires an exact active publication generation"
                )
            existing = db.execute(
                """SELECT cache_id, vector_sha256, payload_json
                   FROM codex_dense_cache_v2
                   WHERE project_id=? AND source_id=? AND generation_id=?
                     AND acl_ref=? AND unit_id=?
                     AND profile_sha256=? AND content_sha256=?""",
                (
                    entry.project_id,
                    entry.source_id,
                    entry.generation_id,
                    entry.acl_ref,
                    entry.unit_id,
                    entry.profile_sha256,
                    entry.content_sha256,
                ),
            ).fetchone()
            if existing:
                if (
                    existing["cache_id"] != entry.cache_id
                    or existing["vector_sha256"] != entry.vector_sha256
                    or existing["payload_json"] != payload
                ):
                    raise CodexV2PublicationError("dense cache identity collision")
                return "already_cached"
            db.execute(
                """INSERT INTO codex_dense_cache_v2
                   (cache_id, project_id, source_id, generation_id, acl_ref, unit_id,
                    profile_sha256, content_sha256, vector_sha256, active, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
                (
                    entry.cache_id,
                    entry.project_id,
                    entry.source_id,
                    entry.generation_id,
                    entry.acl_ref,
                    entry.unit_id,
                    entry.profile_sha256,
                    entry.content_sha256,
                    entry.vector_sha256,
                    payload,
                ),
            )
        return "cached"

    def get_dense_cache(
        self,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        acl_ref: str,
        unit_id: str,
        profile_sha256: str,
        content_sha256: str,
    ) -> CodexDenseCacheEntryV2 | None:
        with self._connection() as db:
            row = db.execute(
                """SELECT c.payload_json FROM codex_dense_cache_v2 AS c
                   JOIN codex_generations_v2 AS g
                     ON g.generation_id=c.generation_id
                   LEFT JOIN codex_tombstones_v2 AS t
                     ON t.project_id=c.project_id AND t.source_id=c.source_id
                    AND t.generation_id=c.generation_id
                   WHERE c.project_id=? AND c.source_id=? AND c.generation_id=?
                     AND c.acl_ref=? AND c.unit_id=?
                     AND c.profile_sha256=? AND c.content_sha256=?
                     AND c.active=1 AND g.active=1 AND g.status='active'
                     AND t.tombstone_id IS NULL""",
                (
                    project_id,
                    source_id,
                    generation_id,
                    acl_ref,
                    unit_id,
                    profile_sha256,
                    content_sha256,
                ),
            ).fetchone()
        return (
            CodexDenseCacheEntryV2.model_validate(json.loads(row["payload_json"])) if row else None
        )

    def publish_calibration(self, artifact: CodexCalibrationArtifactV2) -> str:
        artifact = CodexCalibrationArtifactV2.model_validate(
            artifact.model_dump(mode="python", round_trip=True)
        )
        payload = _canonical_json(artifact.model_dump(mode="json"))
        with self._transaction() as db:
            existing = db.execute(
                """SELECT payload_json FROM codex_calibration_v2
                   WHERE artifact_sha256=?""",
                (artifact.artifact_sha256,),
            ).fetchone()
            if existing:
                if existing["payload_json"] != payload:
                    raise CodexV2PublicationError("calibration artifact collision")
                return "already_published"
            db.execute(
                """INSERT INTO codex_calibration_v2
                   (artifact_sha256, task, payload_json) VALUES (?, ?, ?)""",
                (artifact.artifact_sha256, artifact.task, payload),
            )
        return "published"

    def publish_append_cursor(self, cursor: object) -> dict[str, Any]:
        from .governance_v2 import (  # local import avoids a module cycle
            CodexAppendCursorV2,
            CodexAppendDisposition,
        )

        exact = CodexAppendCursorV2.model_validate(
            cursor.model_dump(mode="python", round_trip=True)  # type: ignore[attr-defined]
        )
        payload = _canonical_json(exact.model_dump(mode="json"))
        with self._transaction() as db:
            tombstone = db.execute(
                """SELECT 1 FROM codex_tombstones_v2
                   WHERE project_id=? AND source_id=? AND generation_id=?""",
                (exact.project_id, exact.source_id, exact.generation_id),
            ).fetchone()
            if tombstone is not None:
                raise CodexV2PublicationError("append cursor generation is privacy-tombstoned")
            generation = db.execute(
                """SELECT project_id, source_id, acl_ref, status, active
                   FROM codex_generations_v2 WHERE generation_id=?""",
                (exact.generation_id,),
            ).fetchone()
            if generation is not None and (
                generation["project_id"] != exact.project_id
                or generation["source_id"] != exact.source_id
                or generation["acl_ref"] != exact.acl_ref
                or generation["status"] != "active"
                or generation["active"] != 1
            ):
                raise CodexV2PublicationError("append cursor generation scope is not active")
            existing = db.execute(
                """SELECT payload_json FROM codex_append_cursors_v2 WHERE cursor_id=?""",
                (exact.cursor_id,),
            ).fetchone()
            if existing:
                if existing["payload_json"] != payload:
                    raise CodexV2PublicationError("append cursor identity collision")
                head = db.execute(
                    """SELECT cursor_id FROM codex_cursor_heads_v2
                       WHERE project_id=? AND source_id=? AND active=1""",
                    (exact.project_id, exact.source_id),
                ).fetchone()
                return {
                    "operation": "already_published",
                    "cursor_id": exact.cursor_id,
                    "head_cursor_id": head["cursor_id"] if head else None,
                }
            head = db.execute(
                """SELECT h.cursor_id, h.active, c.generation_id, c.acl_ref
                   FROM codex_cursor_heads_v2 AS h
                   JOIN codex_append_cursors_v2 AS c ON c.cursor_id=h.cursor_id
                   WHERE h.project_id=? AND h.source_id=?""",
                (exact.project_id, exact.source_id),
            ).fetchone()
            if (
                head
                and head["active"]
                and (
                    head["generation_id"] != exact.generation_id or head["acl_ref"] != exact.acl_ref
                )
            ):
                raise CodexV2PublicationError("append cursor head scope changed")
            expected_previous = head["cursor_id"] if head and head["active"] else None
            if exact.previous_cursor_id != expected_previous:
                raise CodexV2PublicationError("append cursor lost last-known-good concurrency")
            db.execute(
                """INSERT INTO codex_append_cursors_v2
                   (cursor_id, project_id, source_id, generation_id, acl_ref,
                    previous_cursor_id, disposition, cursor_sha256, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    exact.cursor_id,
                    exact.project_id,
                    exact.source_id,
                    exact.generation_id,
                    exact.acl_ref,
                    exact.previous_cursor_id,
                    exact.disposition,
                    exact.cursor_sha256,
                    payload,
                ),
            )
            if exact.disposition is CodexAppendDisposition.FULL_REINGEST_REQUIRED:
                return {
                    "operation": "lkg_retained",
                    "cursor_id": exact.cursor_id,
                    "head_cursor_id": expected_previous,
                }
            db.execute(
                """INSERT INTO codex_cursor_heads_v2
                   (project_id, source_id, cursor_id, active)
                   VALUES (?, ?, ?, 1)
                   ON CONFLICT(project_id, source_id)
                   DO UPDATE SET cursor_id=excluded.cursor_id, active=1""",
                (exact.project_id, exact.source_id, exact.cursor_id),
            )
            return {
                "operation": "published",
                "cursor_id": exact.cursor_id,
                "head_cursor_id": exact.cursor_id,
            }

    def get_append_cursor(self, cursor_id: str) -> object | None:
        from .governance_v2 import CodexAppendCursorV2

        with self._connection() as db:
            row = db.execute(
                """SELECT payload_json FROM codex_append_cursors_v2
                   WHERE cursor_id=?""",
                (cursor_id,),
            ).fetchone()
        if row is None:
            return None
        return CodexAppendCursorV2.model_validate(json.loads(row["payload_json"]))

    def get_append_cursor_head(
        self,
        *,
        project_id: str,
        source_id: str,
        generation_id: str,
        acl_ref: str,
    ) -> object | None:
        from .governance_v2 import CodexAppendCursorV2

        with self._connection() as db:
            row = db.execute(
                """SELECT c.payload_json
                   FROM codex_cursor_heads_v2 AS h
                   JOIN codex_append_cursors_v2 AS c ON c.cursor_id=h.cursor_id
                   LEFT JOIN codex_tombstones_v2 AS t
                     ON t.project_id=c.project_id AND t.source_id=c.source_id
                    AND t.generation_id=c.generation_id
                   WHERE h.project_id=? AND h.source_id=? AND h.active=1
                     AND c.generation_id=? AND c.acl_ref=?
                     AND t.tombstone_id IS NULL""",
                (project_id, source_id, generation_id, acl_ref),
            ).fetchone()
        if row is None:
            return None
        return CodexAppendCursorV2.model_validate(json.loads(row["payload_json"]))

    def publish_release_record(self, evidence: object, decision: object) -> str:
        from .governance_v2 import (
            CodexReleaseDecisionV2,
            CodexReleaseEvidenceV2,
            evaluate_codex_release_v2,
        )

        exact_evidence = CodexReleaseEvidenceV2.model_validate(
            evidence.model_dump(mode="python", round_trip=True)  # type: ignore[attr-defined]
        )
        exact_decision = CodexReleaseDecisionV2.model_validate(
            decision.model_dump(mode="python", round_trip=True)  # type: ignore[attr-defined]
        )
        if (
            exact_decision.observed_stage != exact_evidence.observed_stage
            or exact_decision.proposed_stage != exact_evidence.proposed_stage
        ):
            raise CodexV2PublicationError("release decision does not bind its evidence")
        if exact_decision != evaluate_codex_release_v2(exact_evidence):
            raise CodexV2PublicationError(
                "release decision is not the exact deterministic evaluation"
            )
        evidence_json = _canonical_json(exact_evidence.model_dump(mode="json"))
        decision_json = _canonical_json(exact_decision.model_dump(mode="json"))
        with self._transaction() as db:
            existing = db.execute(
                """SELECT evidence_json, decision_json FROM codex_release_records_v2
                   WHERE evidence_id=?""",
                (exact_evidence.evidence_id,),
            ).fetchone()
            if existing:
                if (
                    existing["evidence_json"] != evidence_json
                    or existing["decision_json"] != decision_json
                ):
                    raise CodexV2PublicationError("release record identity collision")
                return "already_published"
            db.execute(
                """INSERT INTO codex_release_records_v2
                   (evidence_id, decision, evidence_json, decision_json)
                   VALUES (?, ?, ?, ?)""",
                (
                    exact_evidence.evidence_id,
                    exact_decision.decision,
                    evidence_json,
                    decision_json,
                ),
            )
        return "published"

    def _summary(
        self,
        db: sqlite3.Connection,
        publication_id: str,
        operation: str,
    ) -> dict[str, Any]:
        row = db.execute(
            """SELECT p.publication_id, p.publication_sha256, p.generation_id,
                      g.status, g.active
               FROM codex_publications_v2 p
               JOIN codex_generations_v2 g ON g.generation_id=p.generation_id
               WHERE p.publication_id=?""",
            (publication_id,),
        ).fetchone()
        if not row:
            raise CodexV2PublicationError("generation summary is unavailable")
        counts = {
            table: db.execute(
                f"SELECT COUNT(*) AS value FROM {table} WHERE publication_id=?",  # noqa: S608
                (publication_id,),
            ).fetchone()["value"]
            for table in (
                "codex_episode_versions_v2",
                "codex_retrieval_units_v2",
                "codex_temporal_edges_v2",
            )
        }
        return {
            "operation": operation,
            "publication_id": row["publication_id"],
            "publication_sha256": row["publication_sha256"],
            "generation_id": row["generation_id"],
            "status": row["status"],
            "active": bool(row["active"]),
            "counts": counts,
            "schema_version": CODEX_V2_SCHEMA_VERSION,
        }


__all__ = [
    "CODEX_V2_SCHEMA",
    "CODEX_V2_SCHEMA_VERSION",
    "CodexV2PublicationError",
    "CodexV2Store",
    "CodexV2StoreError",
    "CodexV2StorePathError",
]
