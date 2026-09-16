"""Isolated, generation-consistent Wiki store and portable snapshot verifier."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..multisource_foundation_v2 import MultiSourceCandidateV2
from .builder_v1 import (
    WikiBuilderDecisionV1,
    WikiBuilderGenerationBaseV1,
    WikiBuilderPatchV1,
    build_wiki_builder_generation_base_v1,
)
from .cache_v1 import (
    WikiSnapshotCacheKeyV1,
    WikiSnapshotCacheStatsV1,
    WikiSnapshotCacheV1,
)
from .compiler_v1 import WikiCompilationResultV1, WikiErrorBookEntryV1
from .contracts_v1 import (
    WIKI_SNAPSHOT_VERSION,
    WikiDirectoryV1,
    WikiGenerationManifestV1,
    WikiGenerationStatusV1,
    WikiLinkStatusV1,
    WikiPageFragmentV1,
    WikiRecordIndexV1,
    WikiRecordKindV1,
    canonical_json_bytes_v1,
    canonical_sha256_v1,
    visibility_partition_v1,
)
from .paths_v1 import validate_wiki_logical_path_v1

WIKI_STORE_SCHEMA_VERSION = "agent-native-wiki-store-schema-v3"
WIKI_SNAPSHOT_PACKAGE_VERSION = "wiki-portable-snapshot-v1"
WIKI_SNAPSHOT_FILES = (
    "checksums.json",
    "manifest.json",
    "records.jsonl",
    "snapshot.json",
)
_SNAPSHOT_TARGETS = ("manifest.json", "records.jsonl", "snapshot.json")
_SHA256_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SEARCH_TOKEN_RE = re.compile(r"[a-z0-9_@.+-]+|[\u3400-\u9fff]")

WikiRecordV1 = WikiDirectoryV1 | WikiPageFragmentV1
Identifier = Annotated[str, StringConstraints(min_length=1, max_length=240)]


class WikiStoreError(ValueError):
    """Base Wiki Store failure."""


class WikiStorePathError(WikiStoreError):
    """Raised for unsafe or service-owned paths."""


class WikiPublicationError(WikiStoreError):
    """Raised when a snapshot cannot be staged or atomically published."""


class _FrozenStore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class WikiSnapshotDescriptionV1(_FrozenStore):
    package_version: Literal[WIKI_SNAPSHOT_PACKAGE_VERSION] = WIKI_SNAPSHOT_PACKAGE_VERSION
    snapshot_version: Literal[WIKI_SNAPSHOT_VERSION] = WIKI_SNAPSHOT_VERSION
    project_id: Identifier
    generation_id: Identifier
    manifest_sha256: str
    ordered_record_sha256: tuple[str, ...]
    record_count: int = Field(ge=1)
    retrieval_executed: Literal[False] = False
    database_included: Literal[False] = False
    portable: Literal[True] = True
    content_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiSnapshotDescriptionV1:
        if self.record_count != len(self.ordered_record_sha256):
            raise WikiPublicationError("portable snapshot record denominator mismatch")
        if self.content_sha256 != canonical_sha256_v1(
            self.model_dump(mode="json", exclude={"content_sha256"})
        ):
            raise WikiPublicationError("portable snapshot description digest mismatch")
        return self


class WikiSnapshotChecksumsV1(_FrozenStore):
    package_version: Literal[WIKI_SNAPSHOT_PACKAGE_VERSION] = WIKI_SNAPSHOT_PACKAGE_VERSION
    checksums: tuple[tuple[str, str], ...]
    package_set_sha256: str

    @model_validator(mode="after")
    def _identity(self) -> WikiSnapshotChecksumsV1:
        if tuple(item[0] for item in self.checksums) != _SNAPSHOT_TARGETS:
            raise WikiPublicationError("portable snapshot checksum membership mismatch")
        if any(not _SHA256_RE.fullmatch(item[1]) for item in self.checksums):
            raise WikiPublicationError("portable snapshot checksum is malformed")
        if self.package_set_sha256 != canonical_sha256_v1(dict(self.checksums)):
            raise WikiPublicationError("portable snapshot set digest mismatch")
        return self


class WikiSnapshotVerificationV1(_FrozenStore):
    project_id: Identifier
    generation_id: Identifier
    manifest_sha256: str
    record_count: int
    package_set_sha256: str
    retrieval_executed: Literal[False] = False
    database_opened: Literal[False] = False
    portable: Literal[True] = True
    status: Literal["VERIFIED_WIKI_SNAPSHOT"] = "VERIFIED_WIKI_SNAPSHOT"


def _strict_json_loads(raw: bytes) -> object:
    def reject_constant(value: str) -> None:
        raise WikiPublicationError(f"non-finite JSON value is forbidden: {value}")

    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WikiPublicationError("duplicate JSON keys are forbidden")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=no_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WikiPublicationError("portable snapshot JSON is invalid") from error


def _canonical_file_bytes(value: object) -> bytes:
    return canonical_json_bytes_v1(value) + b"\n"


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _path_has_symlink(path: Path, stop: Path) -> bool:
    current = path
    while current != stop:
        if current.exists() and current.is_symlink():
            return True
        if current.parent == current:
            return True
        current = current.parent
    return stop.exists() and stop.is_symlink()


def _record_kind(record: WikiRecordV1) -> WikiRecordKindV1:
    return (
        WikiRecordKindV1.DIRECTORY
        if isinstance(record, WikiDirectoryV1)
        else WikiRecordKindV1.PAGE_FRAGMENT
    )


def _record_index_key(record: WikiRecordV1) -> tuple[str, str, str]:
    return (record.scope.visibility_partition, record.logical_path, _record_kind(record).value)


def _record_from_payload(kind: WikiRecordKindV1, payload: object) -> WikiRecordV1:
    model = WikiDirectoryV1 if kind is WikiRecordKindV1.DIRECTORY else WikiPageFragmentV1
    return model.model_validate(payload)


def _page_search_columns(page: WikiPageFragmentV1) -> tuple[str, ...]:
    return (
        page.logical_path,
        page.title,
        page.summary,
        " ".join(page.aliases),
        " ".join(page.tags),
        " ".join(page.evidence_roles),
        " ".join(item.object_text or item.object_path or "" for item in page.facts),
        " ".join(
            f"{item.relation} {item.inverse_relation} {item.target_path}" for item in page.links
        ),
    )


class WikiStoreV1:
    """A dedicated SQLite store; never points at the service-owned evidence database."""

    def __init__(
        self,
        database_path: Path,
        *,
        isolated_root: Path,
        cache_max_entries: int = 4_096,
        cache_max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if not database_path.is_absolute() or not isolated_root.is_absolute():
            raise WikiStorePathError("Wiki Store paths must be absolute")
        forbidden_names = {
            "evidence-rag.sqlite3",
            "evidence-rag.sqlite3-wal",
            "evidence-rag.sqlite3-shm",
        }
        if database_path.name in forbidden_names:
            raise WikiStorePathError("formal service database is forbidden")
        raw_root = Path(os.path.abspath(isolated_root))
        raw_path = Path(os.path.abspath(database_path))
        if raw_path == raw_root or not raw_path.is_relative_to(raw_root):
            raise WikiStorePathError("Wiki Store must be inside its isolated root")
        raw_root.mkdir(parents=True, exist_ok=True)
        if _path_has_symlink(raw_path.parent, raw_root):
            raise WikiStorePathError("Wiki Store path traverses a symlink")
        root = raw_root.resolve(strict=True)
        path = raw_path.resolve(strict=False)
        if path == root or not path.is_relative_to(root):
            raise WikiStorePathError("Wiki Store escapes its isolated root")
        if path.exists():
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or path.is_symlink():
                raise WikiStorePathError("Wiki Store must be a regular file")
        for suffix in ("-wal", "-shm"):
            if Path(str(path) + suffix).exists():
                raise WikiStorePathError("Wiki Store sidecars are forbidden")
        self.path = path
        self.root = root
        self._record_cache = WikiSnapshotCacheV1(
            max_entries=cache_max_entries,
            max_bytes=cache_max_bytes,
        )

    def cache_stats(self) -> WikiSnapshotCacheStatsV1:
        """Return bounded counters without exposing ACL-partitioned cache keys."""

        return self._record_cache.stats()

    @staticmethod
    def _cache_key(
        *,
        project_id: str,
        generation_id: str,
        visibility_partition: str,
        logical_path: str,
        record_kind: WikiRecordKindV1,
    ) -> WikiSnapshotCacheKeyV1:
        return WikiSnapshotCacheKeyV1(
            project_id=project_id,
            generation_id=generation_id,
            visibility_partition=visibility_partition,
            logical_path=logical_path,
            record_kind=record_kind,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=MEMORY")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _assert_no_sidecars(self) -> None:
        if any(Path(str(self.path) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise WikiStoreError("isolated Wiki Store created sidecars")

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS wiki_schema_meta_v1(
                    schema_version TEXT PRIMARY KEY,
                    installed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wiki_generations_v1(
                    project_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL UNIQUE,
                    manifest_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('staged','verified','published','retired')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, generation_id)
                );
                CREATE TABLE IF NOT EXISTS wiki_records_v1(
                    physical_key TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    visibility_partition TEXT NOT NULL,
                    logical_path TEXT NOT NULL,
                    record_kind TEXT NOT NULL CHECK(record_kind IN ('directory','page_fragment')),
                    record_sha256 TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    FOREIGN KEY(project_id, generation_id)
                        REFERENCES wiki_generations_v1(project_id, generation_id)
                        ON DELETE RESTRICT,
                    UNIQUE(project_id, generation_id, visibility_partition, logical_path, record_kind)
                );
                CREATE TABLE IF NOT EXISTS wiki_active_generations_v1(
                    project_id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    FOREIGN KEY(project_id, generation_id)
                        REFERENCES wiki_generations_v1(project_id, generation_id)
                        ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS wiki_source_candidates_v1(
                    project_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    candidate_sha256 TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    source_generation TEXT NOT NULL,
                    acl_ref TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    PRIMARY KEY(project_id, generation_id, candidate_sha256),
                    FOREIGN KEY(project_id, generation_id)
                        REFERENCES wiki_generations_v1(project_id, generation_id)
                        ON DELETE RESTRICT,
                    UNIQUE(project_id, generation_id, candidate_id)
                );
                CREATE TABLE IF NOT EXISTS wiki_compilations_v1(
                    project_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    result_sha256 TEXT NOT NULL UNIQUE,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY(project_id, generation_id),
                    FOREIGN KEY(project_id, generation_id)
                        REFERENCES wiki_generations_v1(project_id, generation_id)
                        ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS wiki_error_book_v1(
                    project_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    entry_id TEXT NOT NULL,
                    entry_sha256 TEXT NOT NULL,
                    entry_json TEXT NOT NULL,
                    PRIMARY KEY(project_id, generation_id, entry_id),
                    FOREIGN KEY(project_id, generation_id)
                        REFERENCES wiki_generations_v1(project_id, generation_id)
                        ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS wiki_builder_patches_v1(
                    project_id TEXT NOT NULL,
                    patch_id TEXT NOT NULL,
                    base_manifest_sha256 TEXT NOT NULL,
                    patch_sha256 TEXT NOT NULL UNIQUE,
                    patch_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('proposed','reviewed','evaluated','rejected')),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, patch_id)
                );
                CREATE TABLE IF NOT EXISTS wiki_builder_decisions_v1(
                    project_id TEXT NOT NULL,
                    patch_id TEXT NOT NULL,
                    decision_sha256 TEXT NOT NULL UNIQUE,
                    decision_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, patch_id),
                    FOREIGN KEY(project_id, patch_id)
                        REFERENCES wiki_builder_patches_v1(project_id, patch_id)
                        ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS wiki_builder_staging_v1(
                    project_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    patch_id TEXT NOT NULL,
                    decision_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, generation_id),
                    FOREIGN KEY(project_id, generation_id)
                        REFERENCES wiki_generations_v1(project_id, generation_id)
                        ON DELETE RESTRICT,
                    FOREIGN KEY(project_id, patch_id)
                        REFERENCES wiki_builder_decisions_v1(project_id, patch_id)
                        ON DELETE RESTRICT
                );
                CREATE TABLE IF NOT EXISTS wiki_builder_generation_bases_v1(
                    project_id TEXT NOT NULL,
                    generation_id TEXT NOT NULL,
                    base_sha256 TEXT NOT NULL UNIQUE,
                    base_json TEXT NOT NULL,
                    PRIMARY KEY(project_id, generation_id),
                    FOREIGN KEY(project_id, generation_id)
                        REFERENCES wiki_generations_v1(project_id, generation_id)
                        ON DELETE RESTRICT
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS wiki_page_fts_v1 USING fts5(
                    physical_key UNINDEXED,
                    project_id UNINDEXED,
                    generation_id UNINDEXED,
                    visibility_partition UNINDEXED,
                    page_sha256 UNINDEXED,
                    logical_path,
                    title,
                    summary,
                    aliases,
                    tags,
                    roles,
                    facts,
                    links,
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE INDEX IF NOT EXISTS wiki_record_lookup_v1
                    ON wiki_records_v1(project_id, generation_id, visibility_partition, logical_path);
                CREATE INDEX IF NOT EXISTS wiki_candidate_lookup_v1
                    ON wiki_source_candidates_v1(
                        project_id, generation_id, source, source_generation, acl_ref
                    );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO wiki_schema_meta_v1(schema_version, installed_at)
                VALUES (?, ?)
                """,
                (WIKI_STORE_SCHEMA_VERSION, "2026-08-03T00:00:00Z"),
            )
        self._assert_no_sidecars()

    @staticmethod
    def _validate_record_set(
        manifest: WikiGenerationManifestV1,
        records: tuple[WikiRecordV1, ...],
    ) -> tuple[WikiRecordV1, ...]:
        if manifest.status is not WikiGenerationStatusV1.VERIFIED:
            raise WikiPublicationError("only a verified immutable manifest can be staged")
        if not records:
            raise WikiPublicationError("Wiki generation cannot be empty")
        ordered = tuple(
            sorted(
                records,
                key=lambda item: (
                    item.scope.visibility_partition,
                    item.logical_path,
                    _record_kind(item).value,
                ),
            )
        )
        keys = tuple(_record_index_key(item) for item in ordered)
        if keys != tuple(sorted(set(keys))):
            raise WikiPublicationError("Wiki generation records must be unique")
        manifest_keys = tuple(
            (item.visibility_partition, item.logical_path, item.record_kind.value)
            for item in manifest.records
        )
        if keys != manifest_keys:
            raise WikiPublicationError("Wiki generation record membership differs from manifest")
        expected_source_generations = {item.source: item for item in manifest.source_generations}
        by_path: dict[tuple[str, str], WikiRecordV1] = {}
        for record, index in zip(ordered, manifest.records, strict=True):
            if record.scope.project_id != manifest.project_id:
                raise WikiPublicationError("Wiki record belongs to another project")
            if any(
                expected_source_generations.get(item.source) != item
                for item in record.scope.source_generations
            ):
                raise WikiPublicationError("Wiki record mixes source generations")
            if record.content_sha256 != index.record_sha256:
                raise WikiPublicationError("Wiki record digest differs from manifest")
            by_path[(record.scope.visibility_partition, record.logical_path)] = record
        for record in ordered:
            partition = record.scope.visibility_partition
            if isinstance(record, WikiDirectoryV1):
                for path in (*record.child_paths, *record.page_paths):
                    if (partition, path) not in by_path:
                        raise WikiPublicationError("Wiki directory contains a dangling child")
            else:
                for link in record.links:
                    if (
                        link.status is WikiLinkStatusV1.ACTIVE
                        and (partition, link.target_path) not in by_path
                    ):
                        raise WikiPublicationError(
                            "active Wiki link has no visible target in the snapshot"
                        )
                for fact in record.facts:
                    if fact.object_path and (partition, fact.object_path) not in by_path:
                        raise WikiPublicationError(
                            "Wiki fact entity object is absent from the snapshot"
                        )
        for partition in manifest.visibility_partitions:
            roots = {
                item.logical_path
                for item in ordered
                if isinstance(item, WikiDirectoryV1)
                and item.scope.visibility_partition == partition
            }
            if not set(manifest.root_paths).issubset(roots):
                raise WikiPublicationError(
                    "visibility partition lacks the complete intent root space"
                )
        return ordered

    def stage_generation(
        self,
        manifest: WikiGenerationManifestV1,
        records: tuple[WikiRecordV1, ...],
        *,
        candidates: tuple[MultiSourceCandidateV2, ...] = (),
        error_book: tuple[WikiErrorBookEntryV1, ...] = (),
        compilation_result: WikiCompilationResultV1 | None = None,
        builder_base: WikiBuilderGenerationBaseV1 | None = None,
    ) -> None:
        ordered = self._validate_record_set(manifest, records)
        if compilation_result is not None and (
            compilation_result.manifest != manifest or compilation_result.records != ordered
        ):
            raise WikiPublicationError("Wiki compilation result differs from staged generation")
        if compilation_result is not None and builder_base is not None:
            raise WikiPublicationError("Wiki generation cannot have two base authorities")
        if builder_base is not None and (
            builder_base.manifest != manifest or builder_base.records != ordered
        ):
            raise WikiPublicationError("Wiki Builder base differs from staged generation")
        candidate_ids = tuple(item.candidate_id for item in candidates)
        if candidate_ids != tuple(sorted(set(candidate_ids))):
            raise WikiPublicationError("Wiki source candidates must be ID-sorted and unique")
        generation_by_source = {
            item.source.value: item.generation_id for item in manifest.source_generations
        }
        allowed_acl_sets = {
            tuple(page.scope.acl_refs) for page in records if isinstance(page, WikiPageFragmentV1)
        }
        for candidate in candidates:
            if generation_by_source.get(candidate.retrieval_domain) != candidate.source_generation:
                raise WikiPublicationError("Wiki candidate source generation differs from manifest")
            if (candidate.acl_ref,) not in allowed_acl_sets:
                raise WikiPublicationError(
                    "Wiki candidate ACL has no published visibility partition"
                )
        entry_ids = tuple(item.entry_id for item in error_book)
        if entry_ids != tuple(sorted(set(entry_ids))):
            raise WikiPublicationError("Wiki Error Book entries must be ID-sorted and unique")
        self.initialize()
        manifest_json = canonical_json_bytes_v1(manifest.model_dump(mode="json")).decode()
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT manifest_sha256 FROM wiki_generations_v1
                WHERE project_id=? AND generation_id=?
                """,
                (manifest.project_id, manifest.generation_id),
            ).fetchone()
            if existing:
                if existing["manifest_sha256"] != manifest.content_sha256:
                    raise WikiPublicationError(
                        "generation ID already exists with different authority"
                    )
                return
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO wiki_generations_v1(
                        project_id, generation_id, manifest_sha256, manifest_json, state, created_at
                    ) VALUES (?, ?, ?, ?, 'staged', ?)
                    """,
                    (
                        manifest.project_id,
                        manifest.generation_id,
                        manifest.content_sha256,
                        manifest_json,
                        manifest.created_at,
                    ),
                )
                # Descendants are written before their parents.  The active pointer is
                # still unchanged, so readers cannot observe this staging generation.
                child_first = sorted(
                    zip(ordered, manifest.records, strict=True),
                    key=lambda pair: (-pair[0].logical_path.count("/"), pair[0].logical_path),
                )
                for record, index in child_first:
                    connection.execute(
                        """
                        INSERT INTO wiki_records_v1(
                            physical_key, project_id, generation_id, visibility_partition,
                            logical_path, record_kind, record_sha256, record_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            index.physical_key,
                            manifest.project_id,
                            manifest.generation_id,
                            index.visibility_partition,
                            index.logical_path,
                            index.record_kind.value,
                            index.record_sha256,
                            canonical_json_bytes_v1(record.model_dump(mode="json")).decode(),
                        ),
                    )
                    if isinstance(record, WikiPageFragmentV1):
                        columns = _page_search_columns(record)
                        connection.execute(
                            """
                            INSERT INTO wiki_page_fts_v1(
                                physical_key, project_id, generation_id,
                                visibility_partition, page_sha256, logical_path,
                                title, summary, aliases, tags, roles, facts, links
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                index.physical_key,
                                manifest.project_id,
                                manifest.generation_id,
                                index.visibility_partition,
                                index.record_sha256,
                                *columns,
                            ),
                        )
                for candidate in candidates:
                    candidate_payload = candidate.model_dump(mode="json")
                    connection.execute(
                        """
                        INSERT INTO wiki_source_candidates_v1(
                            project_id, generation_id, candidate_sha256, candidate_id,
                            source, source_generation, acl_ref, candidate_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            manifest.project_id,
                            manifest.generation_id,
                            canonical_sha256_v1(candidate_payload),
                            candidate.candidate_id,
                            candidate.retrieval_domain,
                            candidate.source_generation,
                            candidate.acl_ref,
                            canonical_json_bytes_v1(candidate_payload).decode(),
                        ),
                    )
                for entry in error_book:
                    connection.execute(
                        """
                        INSERT INTO wiki_error_book_v1(
                            project_id, generation_id, entry_id, entry_sha256, entry_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            manifest.project_id,
                            manifest.generation_id,
                            entry.entry_id,
                            entry.content_sha256,
                            canonical_json_bytes_v1(entry.model_dump(mode="json")).decode(),
                        ),
                    )
                if compilation_result is not None:
                    connection.execute(
                        """
                        INSERT INTO wiki_compilations_v1(
                            project_id, generation_id, result_sha256, result_json
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            manifest.project_id,
                            manifest.generation_id,
                            compilation_result.content_sha256,
                            canonical_json_bytes_v1(
                                compilation_result.model_dump(mode="json")
                            ).decode(),
                        ),
                    )
                if builder_base is not None:
                    connection.execute(
                        """
                        INSERT INTO wiki_builder_generation_bases_v1(
                            project_id, generation_id, base_sha256, base_json
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            manifest.project_id,
                            manifest.generation_id,
                            builder_base.content_sha256,
                            canonical_json_bytes_v1(builder_base.model_dump(mode="json")).decode(),
                        ),
                    )
                connection.execute(
                    """
                    UPDATE wiki_generations_v1 SET state='verified'
                    WHERE project_id=? AND generation_id=?
                    """,
                    (manifest.project_id, manifest.generation_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        self.verify_generation(
            project_id=manifest.project_id,
            generation_id=manifest.generation_id,
            expected_manifest_sha256=manifest.content_sha256,
        )
        self._assert_no_sidecars()

    def verify_generation(
        self,
        *,
        project_id: str,
        generation_id: str,
        expected_manifest_sha256: str,
    ) -> WikiGenerationManifestV1:
        with self._connect() as connection:
            generation = connection.execute(
                """
                SELECT manifest_sha256, manifest_json, state FROM wiki_generations_v1
                WHERE project_id=? AND generation_id=?
                """,
                (project_id, generation_id),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT visibility_partition, logical_path, record_kind,
                       record_sha256, physical_key, record_json
                FROM wiki_records_v1
                WHERE project_id=? AND generation_id=?
                ORDER BY visibility_partition, logical_path, record_kind
                """,
                (project_id, generation_id),
            ).fetchall()
            fts_rows = connection.execute(
                """
                SELECT physical_key, page_sha256, logical_path, title, summary,
                       aliases, tags, roles, facts, links
                FROM wiki_page_fts_v1
                WHERE project_id=? AND generation_id=?
                ORDER BY physical_key
                """,
                (project_id, generation_id),
            ).fetchall()
        if not generation or generation["manifest_sha256"] != expected_manifest_sha256:
            raise WikiPublicationError("Wiki generation manifest authority is unavailable")
        manifest = WikiGenerationManifestV1.model_validate(json.loads(generation["manifest_json"]))
        if manifest.content_sha256 != expected_manifest_sha256:
            raise WikiPublicationError("stored Wiki manifest digest mismatch")
        records: list[WikiRecordV1] = []
        observed_indexes: list[WikiRecordIndexV1] = []
        for row in rows:
            kind = WikiRecordKindV1(row["record_kind"])
            record = _record_from_payload(kind, json.loads(row["record_json"]))
            if record.content_sha256 != row["record_sha256"]:
                raise WikiPublicationError("stored Wiki record content mismatch")
            records.append(record)
            observed_indexes.append(
                WikiRecordIndexV1(
                    logical_path=row["logical_path"],
                    record_kind=kind,
                    visibility_partition=row["visibility_partition"],
                    record_sha256=row["record_sha256"],
                    physical_key=row["physical_key"],
                )
            )
        if tuple(observed_indexes) != manifest.records:
            raise WikiPublicationError("stored Wiki record index differs from manifest")
        self._validate_record_set(manifest, tuple(records))
        expected_fts = tuple(
            sorted(
                (
                    index.physical_key,
                    index.record_sha256,
                    *_page_search_columns(record),
                )
                for record, index in zip(records, manifest.records, strict=True)
                if isinstance(record, WikiPageFragmentV1)
            )
        )
        observed_fts = tuple(
            (
                row["physical_key"],
                row["page_sha256"],
                row["logical_path"],
                row["title"],
                row["summary"],
                row["aliases"],
                row["tags"],
                row["roles"],
                row["facts"],
                row["links"],
            )
            for row in fts_rows
        )
        if observed_fts != expected_fts:
            raise WikiPublicationError("Wiki FTS index differs from page authority")
        self._assert_no_sidecars()
        return manifest

    def publish_generation(
        self,
        *,
        project_id: str,
        generation_id: str,
        expected_manifest_sha256: str,
        published_at: str,
    ) -> WikiGenerationManifestV1:
        manifest = self.verify_generation(
            project_id=project_id,
            generation_id=generation_id,
            expected_manifest_sha256=expected_manifest_sha256,
        )
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                state = connection.execute(
                    """
                    SELECT state FROM wiki_generations_v1
                    WHERE project_id=? AND generation_id=? AND manifest_sha256=?
                    """,
                    (project_id, generation_id, expected_manifest_sha256),
                ).fetchone()
                if not state or state["state"] not in {"verified", "published"}:
                    raise WikiPublicationError("only a verified generation can become active")
                previous = connection.execute(
                    "SELECT generation_id FROM wiki_active_generations_v1 WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                connection.execute(
                    """
                    INSERT INTO wiki_active_generations_v1(
                        project_id, generation_id, manifest_sha256, published_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET
                        generation_id=excluded.generation_id,
                        manifest_sha256=excluded.manifest_sha256,
                        published_at=excluded.published_at
                    """,
                    (project_id, generation_id, expected_manifest_sha256, published_at),
                )
                connection.execute(
                    """
                    UPDATE wiki_generations_v1 SET state='published'
                    WHERE project_id=? AND generation_id=?
                    """,
                    (project_id, generation_id),
                )
                if previous and previous["generation_id"] != generation_id:
                    connection.execute(
                        """
                        UPDATE wiki_generations_v1 SET state='retired'
                        WHERE project_id=? AND generation_id=?
                        """,
                        (project_id, previous["generation_id"]),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        self._assert_no_sidecars()
        return manifest

    def rollback_generation(
        self,
        *,
        project_id: str,
        target_generation_id: str,
        expected_target_manifest_sha256: str,
        expected_active_generation_id: str,
        expected_active_manifest_sha256: str,
        rolled_back_at: str,
    ) -> WikiGenerationManifestV1:
        """Atomically restore one retired, digest-pinned generation.

        Rollback is deliberately stricter than publication: both the currently active
        generation and the target generation are compare-and-swapped by immutable
        identities.  A stale operator request therefore cannot roll the project across an
        intervening publication.
        """

        if target_generation_id == expected_active_generation_id:
            raise WikiPublicationError("rollback target must differ from the active generation")
        target = self.verify_generation(
            project_id=project_id,
            generation_id=target_generation_id,
            expected_manifest_sha256=expected_target_manifest_sha256,
        )
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                active = connection.execute(
                    """
                    SELECT a.generation_id, a.manifest_sha256, g.state
                    FROM wiki_active_generations_v1 a
                    JOIN wiki_generations_v1 g
                      ON g.project_id=a.project_id AND g.generation_id=a.generation_id
                    WHERE a.project_id=?
                    """,
                    (project_id,),
                ).fetchone()
                if (
                    active is None
                    or active["generation_id"] != expected_active_generation_id
                    or active["manifest_sha256"] != expected_active_manifest_sha256
                    or active["state"] != "published"
                ):
                    raise WikiPublicationError("active Wiki generation changed before rollback")
                target_state = connection.execute(
                    """
                    SELECT state, manifest_sha256 FROM wiki_generations_v1
                    WHERE project_id=? AND generation_id=?
                    """,
                    (project_id, target_generation_id),
                ).fetchone()
                if (
                    target_state is None
                    or target_state["state"] != "retired"
                    or target_state["manifest_sha256"] != expected_target_manifest_sha256
                ):
                    raise WikiPublicationError(
                        "rollback target is not the exact retired Wiki generation"
                    )
                connection.execute(
                    """
                    UPDATE wiki_active_generations_v1
                    SET generation_id=?, manifest_sha256=?, published_at=?
                    WHERE project_id=?
                    """,
                    (
                        target_generation_id,
                        expected_target_manifest_sha256,
                        rolled_back_at,
                        project_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE wiki_generations_v1 SET state='retired'
                    WHERE project_id=? AND generation_id=?
                    """,
                    (project_id, expected_active_generation_id),
                )
                connection.execute(
                    """
                    UPDATE wiki_generations_v1 SET state='published'
                    WHERE project_id=? AND generation_id=?
                    """,
                    (project_id, target_generation_id),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        self._assert_no_sidecars()
        return target

    def active_manifest(self, project_id: str) -> WikiGenerationManifestV1 | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT g.manifest_json
                FROM wiki_active_generations_v1 a
                JOIN wiki_generations_v1 g
                  ON g.project_id=a.project_id AND g.generation_id=a.generation_id
                WHERE a.project_id=?
                """,
                (project_id,),
            ).fetchone()
        return None if row is None else WikiGenerationManifestV1.model_validate(json.loads(row[0]))

    def read_record(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        logical_path: str,
        record_kind: WikiRecordKindV1,
        generation_id: str | None = None,
    ) -> WikiRecordV1 | None:
        validate_wiki_logical_path_v1(logical_path)
        partition = visibility_partition_v1(requester_acl_refs)
        with self._connect() as connection:
            if generation_id is None:
                active = connection.execute(
                    "SELECT generation_id FROM wiki_active_generations_v1 WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                if not active:
                    return None
                generation_id = str(active["generation_id"])
            key = self._cache_key(
                project_id=project_id,
                generation_id=generation_id,
                visibility_partition=partition,
                logical_path=logical_path,
                record_kind=record_kind,
            )
            cached = self._record_cache.get(key)
            if cached is not None:
                return cached
            row = connection.execute(
                """
                SELECT record_json FROM wiki_records_v1
                WHERE project_id=? AND generation_id=? AND visibility_partition=?
                  AND logical_path=? AND record_kind=?
                """,
                (project_id, generation_id, partition, logical_path, record_kind.value),
            ).fetchone()
        if row is None:
            return None
        record = _record_from_payload(record_kind, json.loads(row[0]))
        self._record_cache.put(key, record)
        return record

    def list_pages(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        generation_id: str | None = None,
        limit: int = 20_000,
    ) -> tuple[WikiPageFragmentV1, ...]:
        if limit < 1 or limit > 20_000:
            raise WikiStoreError("Wiki page scan limit is outside the reviewed bound")
        partition = visibility_partition_v1(requester_acl_refs)
        with self._connect() as connection:
            if generation_id is None:
                active = connection.execute(
                    "SELECT generation_id FROM wiki_active_generations_v1 WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                if not active:
                    return ()
                generation_id = active["generation_id"]
            rows = connection.execute(
                """
                SELECT record_json FROM wiki_records_v1
                WHERE project_id=? AND generation_id=? AND visibility_partition=?
                  AND record_kind='page_fragment'
                ORDER BY logical_path
                LIMIT ?
                """,
                (project_id, generation_id, partition, limit),
            ).fetchall()
        pages = tuple(WikiPageFragmentV1.model_validate(json.loads(row[0])) for row in rows)
        for page in pages:
            self._record_cache.put(
                self._cache_key(
                    project_id=project_id,
                    generation_id=generation_id,
                    visibility_partition=partition,
                    logical_path=page.logical_path,
                    record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
                ),
                page,
            )
        return pages

    def browse_pages(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        generation_id: str | None = None,
        query: str = "",
        page_type: str | None = None,
        source: str | None = None,
        role: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[int, tuple[WikiPageFragmentV1, ...], str | None]:
        """Return an ACL-pinned, server-paginated Wiki catalogue.

        The browser is intentionally separate from hybrid retrieval: it preserves
        logical-path ordering for human review while still using the immutable FTS
        index when a text filter is supplied.  Nested source/role filters are
        evaluated by SQLite JSON1 against the stored, digest-verified page record.
        """

        if offset < 0 or limit < 1 or limit > 200:
            raise WikiStoreError("Wiki catalogue page is outside the reviewed bound")
        tokens = tuple(dict.fromkeys(_SEARCH_TOKEN_RE.findall(query.casefold())))[:64]
        if query.strip() and not tokens:
            return 0, (), generation_id
        partition = visibility_partition_v1(requester_acl_refs)
        with self._connect() as connection:
            if generation_id is None:
                active = connection.execute(
                    "SELECT generation_id FROM wiki_active_generations_v1 WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                if not active:
                    return 0, (), None
                generation_id = str(active["generation_id"])

            predicates = [
                "r.project_id=?",
                "r.generation_id=?",
                "r.visibility_partition=?",
                "r.record_kind='page_fragment'",
            ]
            parameters: list[object] = [project_id, generation_id, partition]
            join = ""
            if tokens:
                match = " OR ".join(f'"{token}"' for token in tokens)
                join = "JOIN wiki_page_fts_v1 f ON f.physical_key=r.physical_key"
                predicates.append("wiki_page_fts_v1 MATCH ?")
                parameters.append(match)
            if page_type:
                predicates.append("json_extract(r.record_json, '$.page_type')=?")
                parameters.append(page_type)
            if source:
                predicates.append(
                    "EXISTS (SELECT 1 FROM json_each(r.record_json, '$.source_refs') ref "
                    "WHERE json_extract(ref.value, '$.source')=?)"
                )
                parameters.append(source)
            if role:
                predicates.append(
                    "EXISTS (SELECT 1 FROM json_each(r.record_json, '$.evidence_roles') roles "
                    "WHERE roles.value=?)"
                )
                parameters.append(role)
            where = " AND ".join(predicates)
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM wiki_records_v1 r {join} WHERE {where}",
                    tuple(parameters),
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT r.record_json FROM wiki_records_v1 r {join}
                WHERE {where}
                ORDER BY r.logical_path
                LIMIT ? OFFSET ?
                """,
                (*parameters, limit, offset),
            ).fetchall()
        pages = tuple(WikiPageFragmentV1.model_validate(json.loads(row[0])) for row in rows)
        for page in pages:
            self._record_cache.put(
                self._cache_key(
                    project_id=project_id,
                    generation_id=generation_id,
                    visibility_partition=partition,
                    logical_path=page.logical_path,
                    record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
                ),
                page,
            )
        return total, pages, generation_id

    def read_pages(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        logical_paths: tuple[str, ...],
        generation_id: str,
    ) -> tuple[WikiPageFragmentV1, ...]:
        if logical_paths != tuple(dict.fromkeys(logical_paths)) or len(logical_paths) > 2_000:
            raise WikiStoreError("Wiki batch page membership is invalid")
        if not logical_paths:
            return ()
        partition = visibility_partition_v1(requester_acl_refs)
        by_path: dict[str, WikiPageFragmentV1] = {}
        misses: list[str] = []
        for logical_path in logical_paths:
            validate_wiki_logical_path_v1(logical_path)
            cached = self._record_cache.get(
                self._cache_key(
                    project_id=project_id,
                    generation_id=generation_id,
                    visibility_partition=partition,
                    logical_path=logical_path,
                    record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
                )
            )
            if cached is None:
                misses.append(logical_path)
            elif isinstance(cached, WikiPageFragmentV1):
                by_path[logical_path] = cached
        if misses:
            with self._connect() as connection:
                for start in range(0, len(misses), 256):
                    batch = misses[start : start + 256]
                    marks = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        f"""
                        SELECT logical_path, record_json FROM wiki_records_v1
                        WHERE project_id=? AND generation_id=? AND visibility_partition=?
                          AND record_kind='page_fragment' AND logical_path IN ({marks})
                        """,
                        (project_id, generation_id, partition, *batch),
                    ).fetchall()
                    for row in rows:
                        page = WikiPageFragmentV1.model_validate(json.loads(row["record_json"]))
                        by_path[row["logical_path"]] = page
                        self._record_cache.put(
                            self._cache_key(
                                project_id=project_id,
                                generation_id=generation_id,
                                visibility_partition=partition,
                                logical_path=row["logical_path"],
                                record_kind=WikiRecordKindV1.PAGE_FRAGMENT,
                            ),
                            page,
                        )
        return tuple(by_path[path] for path in logical_paths if path in by_path)

    def search_page_paths_fts(
        self,
        *,
        project_id: str,
        requester_acl_refs: tuple[str, ...],
        query: str,
        generation_id: str,
        limit: int = 2_000,
    ) -> tuple[str, ...]:
        if not 1 <= limit <= 2_000:
            raise WikiStoreError("Wiki FTS result limit is outside the reviewed bound")
        tokens = tuple(dict.fromkeys(_SEARCH_TOKEN_RE.findall(query.casefold())))[:64]
        if not tokens:
            return ()
        match = " OR ".join(f'"{token}"' for token in tokens)
        partition = visibility_partition_v1(requester_acl_refs)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT logical_path
                FROM wiki_page_fts_v1
                WHERE wiki_page_fts_v1 MATCH ?
                  AND project_id=? AND generation_id=? AND visibility_partition=?
                ORDER BY bm25(wiki_page_fts_v1, 0.0, 0.0, 0.0, 0.0, 0.0,
                               2.5, 3.0, 1.5, 0.8, 0.8, 1.2, 1.0, 0.8),
                         logical_path
                LIMIT ?
                """,
                (match, project_id, generation_id, partition, limit),
            ).fetchall()
        return tuple(str(row["logical_path"]) for row in rows)

    def read_source_candidate(
        self,
        *,
        project_id: str,
        generation_id: str,
        requester_acl_refs: tuple[str, ...],
        candidate_sha256: str,
    ) -> MultiSourceCandidateV2 | None:
        if requester_acl_refs != tuple(sorted(set(requester_acl_refs))):
            raise WikiStoreError("Wiki candidate reader ACL refs must be sorted and unique")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT candidate_sha256, acl_ref, candidate_json
                FROM wiki_source_candidates_v1
                WHERE project_id=? AND generation_id=? AND candidate_sha256=?
                """,
                (project_id, generation_id, candidate_sha256),
            ).fetchone()
        if row is None or row["acl_ref"] not in requester_acl_refs:
            return None
        candidate = MultiSourceCandidateV2.model_validate(json.loads(row["candidate_json"]))
        if canonical_sha256_v1(candidate.model_dump(mode="json")) != row["candidate_sha256"]:
            raise WikiPublicationError("stored Wiki source candidate digest mismatch")
        return candidate

    def list_source_candidates(
        self,
        *,
        project_id: str,
        generation_id: str,
        requester_acl_refs: tuple[str, ...] | None = None,
    ) -> tuple[MultiSourceCandidateV2, ...]:
        clauses = ["project_id=?", "generation_id=?"]
        parameters: list[Any] = [project_id, generation_id]
        if requester_acl_refs is not None:
            if requester_acl_refs != tuple(sorted(set(requester_acl_refs))):
                raise WikiStoreError("Wiki candidate list ACL refs must be sorted and unique")
            if not requester_acl_refs:
                return ()
            marks = ",".join("?" for _ in requester_acl_refs)
            clauses.append(f"acl_ref IN ({marks})")
            parameters.extend(requester_acl_refs)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT candidate_sha256, candidate_json FROM wiki_source_candidates_v1
                WHERE {" AND ".join(clauses)} ORDER BY candidate_id
                """,
                parameters,
            ).fetchall()
        candidates = tuple(
            MultiSourceCandidateV2.model_validate(json.loads(row["candidate_json"])) for row in rows
        )
        if any(
            canonical_sha256_v1(candidate.model_dump(mode="json")) != row["candidate_sha256"]
            for candidate, row in zip(candidates, rows, strict=True)
        ):
            raise WikiPublicationError("stored Wiki source candidate digest mismatch")
        return candidates

    def read_compilation_result(
        self,
        *,
        project_id: str,
        generation_id: str,
    ) -> WikiCompilationResultV1 | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT result_sha256, result_json FROM wiki_compilations_v1
                WHERE project_id=? AND generation_id=?
                """,
                (project_id, generation_id),
            ).fetchone()
        if row is None:
            return None
        result = WikiCompilationResultV1.model_validate(json.loads(row["result_json"]))
        if result.content_sha256 != row["result_sha256"]:
            raise WikiPublicationError("stored Wiki compilation result digest mismatch")
        return result

    def read_generation_base(
        self,
        *,
        project_id: str,
        generation_id: str,
    ) -> WikiBuilderGenerationBaseV1 | None:
        """Resolve either a root Compiler result or a persisted Builder base."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT base_sha256, base_json
                FROM wiki_builder_generation_bases_v1
                WHERE project_id=? AND generation_id=?
                """,
                (project_id, generation_id),
            ).fetchone()
        if row is not None:
            base = WikiBuilderGenerationBaseV1.model_validate(json.loads(row["base_json"]))
            if base.content_sha256 != row["base_sha256"]:
                raise WikiPublicationError("stored Wiki Builder base digest mismatch")
        else:
            compilation = self.read_compilation_result(
                project_id=project_id,
                generation_id=generation_id,
            )
            if compilation is None:
                return None
            candidates = self.list_source_candidates(
                project_id=project_id,
                generation_id=generation_id,
            )
            base = build_wiki_builder_generation_base_v1(
                manifest=compilation.manifest,
                records=compilation.records,
                error_book=compilation.error_book,
                source_candidate_sha256=tuple(
                    sorted(canonical_sha256_v1(item.model_dump(mode="json")) for item in candidates)
                ),
                root_compilation_sha256=compilation.content_sha256,
                parent_manifest_sha256=None,
                applied_patch_sha256=(),
                lineage_depth=0,
            )
        candidates = self.list_source_candidates(
            project_id=project_id,
            generation_id=generation_id,
        )
        observed_candidates = tuple(
            sorted(canonical_sha256_v1(item.model_dump(mode="json")) for item in candidates)
        )
        if observed_candidates != base.source_candidate_sha256:
            raise WikiPublicationError("Wiki Builder base source authority mismatch")
        return base

    def list_error_book(
        self,
        *,
        project_id: str,
        generation_id: str | None = None,
    ) -> tuple[WikiErrorBookEntryV1, ...]:
        with self._connect() as connection:
            if generation_id is None:
                active = connection.execute(
                    "SELECT generation_id FROM wiki_active_generations_v1 WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                if active is None:
                    return ()
                generation_id = str(active["generation_id"])
            rows = connection.execute(
                """
                SELECT entry_sha256, entry_json FROM wiki_error_book_v1
                WHERE project_id=? AND generation_id=? ORDER BY entry_id
                """,
                (project_id, generation_id),
            ).fetchall()
        entries = tuple(
            WikiErrorBookEntryV1.model_validate(json.loads(row["entry_json"])) for row in rows
        )
        if any(
            entry.content_sha256 != row["entry_sha256"]
            for entry, row in zip(entries, rows, strict=True)
        ):
            raise WikiPublicationError("stored Wiki Error Book digest mismatch")
        return entries

    def stage_builder_patch(
        self,
        *,
        project_id: str,
        patch: WikiBuilderPatchV1,
        created_at: str,
    ) -> None:
        active = self.active_manifest(project_id)
        if active is None or active.content_sha256 != patch.base_manifest_sha256:
            raise WikiPublicationError("Wiki patch base is not the active generation")
        state = "reviewed" if patch.reviewed else "proposed"
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT patch_sha256 FROM wiki_builder_patches_v1
                    WHERE project_id=? AND patch_id=?
                    """,
                    (project_id, patch.patch_id),
                ).fetchone()
                if existing is not None and existing["patch_sha256"] != patch.content_sha256:
                    raise WikiPublicationError("Wiki patch ID already binds different content")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO wiki_builder_patches_v1(
                        project_id, patch_id, base_manifest_sha256, patch_sha256,
                        patch_json, state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        patch.patch_id,
                        patch.base_manifest_sha256,
                        patch.content_sha256,
                        canonical_json_bytes_v1(patch.model_dump(mode="json")).decode(),
                        state,
                        created_at,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        self._assert_no_sidecars()

    def list_builder_patches(self, *, project_id: str) -> tuple[WikiBuilderPatchV1, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT patch_sha256, patch_json FROM wiki_builder_patches_v1
                WHERE project_id=? ORDER BY created_at DESC, patch_id
                """,
                (project_id,),
            ).fetchall()
        patches = tuple(
            WikiBuilderPatchV1.model_validate(json.loads(row["patch_json"])) for row in rows
        )
        if any(
            patch.content_sha256 != row["patch_sha256"]
            for patch, row in zip(patches, rows, strict=True)
        ):
            raise WikiPublicationError("stored Wiki patch digest mismatch")
        return patches

    def store_builder_decision(
        self,
        *,
        project_id: str,
        patch_id: str,
        decision: WikiBuilderDecisionV1,
        created_at: str,
    ) -> None:
        if decision.patch_sha256 == "":
            raise WikiPublicationError("Wiki Builder decision lacks patch authority")
        with self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT patch_sha256 FROM wiki_builder_patches_v1
                    WHERE project_id=? AND patch_id=?
                    """,
                    (project_id, patch_id),
                ).fetchone()
                if row is None or row["patch_sha256"] != decision.patch_sha256:
                    raise WikiPublicationError("Wiki Builder decision differs from patch authority")
                connection.execute(
                    """
                    INSERT INTO wiki_builder_decisions_v1(
                        project_id, patch_id, decision_sha256, decision_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, patch_id) DO UPDATE SET
                        decision_sha256=excluded.decision_sha256,
                        decision_json=excluded.decision_json,
                        created_at=excluded.created_at
                    """,
                    (
                        project_id,
                        patch_id,
                        decision.content_sha256,
                        canonical_json_bytes_v1(decision.model_dump(mode="json")).decode(),
                        created_at,
                    ),
                )
                connection.execute(
                    """
                    UPDATE wiki_builder_patches_v1
                    SET state=? WHERE project_id=? AND patch_id=?
                    """,
                    (
                        "rejected" if decision.status.value == "reject" else "evaluated",
                        project_id,
                        patch_id,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        self._assert_no_sidecars()

    def read_builder_decision(
        self,
        *,
        project_id: str,
        patch_id: str,
    ) -> WikiBuilderDecisionV1 | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT decision_sha256, decision_json FROM wiki_builder_decisions_v1
                WHERE project_id=? AND patch_id=?
                """,
                (project_id, patch_id),
            ).fetchone()
        if row is None:
            return None
        decision = WikiBuilderDecisionV1.model_validate(json.loads(row["decision_json"]))
        if decision.content_sha256 != row["decision_sha256"]:
            raise WikiPublicationError("stored Wiki Builder decision digest mismatch")
        return decision

    def register_builder_staging(
        self,
        *,
        project_id: str,
        generation_id: str,
        patch_id: str,
        decision: WikiBuilderDecisionV1,
        created_at: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO wiki_builder_staging_v1(
                    project_id, generation_id, patch_id, decision_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    generation_id,
                    patch_id,
                    decision.content_sha256,
                    created_at,
                ),
            )
        self._assert_no_sidecars()

    def builder_staging_decision(
        self,
        *,
        project_id: str,
        generation_id: str,
    ) -> WikiBuilderDecisionV1 | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT s.decision_sha256, d.decision_json
                FROM wiki_builder_staging_v1 s
                JOIN wiki_builder_decisions_v1 d
                  ON d.project_id=s.project_id AND d.patch_id=s.patch_id
                WHERE s.project_id=? AND s.generation_id=?
                """,
                (project_id, generation_id),
            ).fetchone()
        if row is None:
            return None
        decision = WikiBuilderDecisionV1.model_validate(json.loads(row["decision_json"]))
        if decision.content_sha256 != row["decision_sha256"]:
            raise WikiPublicationError("Wiki Builder staging decision digest mismatch")
        return decision

    def generation_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM wiki_generations_v1").fetchone()
        return int(row[0])

    def export_snapshot(
        self, *, project_id: str, generation_id: str, output_dir: Path
    ) -> WikiSnapshotVerificationV1:
        if not output_dir.is_absolute() or output_dir.exists() or output_dir.is_symlink():
            raise WikiStorePathError("snapshot output must be a new absolute directory")
        if not output_dir.parent.is_dir() or output_dir.parent.is_symlink():
            raise WikiStorePathError("snapshot parent must be a real directory")
        with self._connect() as connection:
            generation = connection.execute(
                """
                SELECT manifest_json FROM wiki_generations_v1
                WHERE project_id=? AND generation_id=?
                """,
                (project_id, generation_id),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT record_kind, record_json FROM wiki_records_v1
                WHERE project_id=? AND generation_id=?
                ORDER BY visibility_partition, logical_path, record_kind
                """,
                (project_id, generation_id),
            ).fetchall()
        if not generation or not rows:
            raise WikiPublicationError("snapshot generation is unavailable")
        manifest = WikiGenerationManifestV1.model_validate(json.loads(generation[0]))
        envelopes = []
        record_digests = []
        for row, index in zip(rows, manifest.records, strict=True):
            payload = json.loads(row["record_json"])
            record = _record_from_payload(WikiRecordKindV1(row["record_kind"]), payload)
            envelopes.append(
                {
                    "index": index.model_dump(mode="json"),
                    "record": record.model_dump(mode="json"),
                }
            )
            record_digests.append(record.content_sha256)
        snapshot_payload = {
            "project_id": project_id,
            "generation_id": generation_id,
            "manifest_sha256": manifest.content_sha256,
            "ordered_record_sha256": tuple(record_digests),
            "record_count": len(record_digests),
        }
        snapshot = WikiSnapshotDescriptionV1(
            **snapshot_payload,
            content_sha256=canonical_sha256_v1(
                {
                    **snapshot_payload,
                    "package_version": WIKI_SNAPSHOT_PACKAGE_VERSION,
                    "snapshot_version": WIKI_SNAPSHOT_VERSION,
                    "retrieval_executed": False,
                    "database_included": False,
                    "portable": True,
                }
            ),
        )
        created = False
        try:
            output_dir.mkdir(mode=0o700)
            created = True
            (output_dir / "manifest.json").write_bytes(
                _canonical_file_bytes(manifest.model_dump(mode="json"))
            )
            records_bytes = b"".join(_canonical_file_bytes(item) for item in envelopes)
            (output_dir / "records.jsonl").write_bytes(records_bytes)
            (output_dir / "snapshot.json").write_bytes(
                _canonical_file_bytes(snapshot.model_dump(mode="json"))
            )
            checksums = tuple(
                (filename, _sha256_bytes((output_dir / filename).read_bytes()))
                for filename in _SNAPSHOT_TARGETS
            )
            checksum_contract = WikiSnapshotChecksumsV1(
                checksums=checksums,
                package_set_sha256=canonical_sha256_v1(dict(checksums)),
            )
            (output_dir / "checksums.json").write_bytes(
                _canonical_file_bytes(checksum_contract.model_dump(mode="json"))
            )
            return verify_wiki_snapshot_v1(output_dir)
        except Exception:
            if created:
                for filename in WIKI_SNAPSHOT_FILES:
                    entry = output_dir / filename
                    if entry.exists() or entry.is_symlink():
                        entry.unlink()
                output_dir.rmdir()
            raise


def verify_wiki_snapshot_v1(package_dir: Path) -> WikiSnapshotVerificationV1:
    """Verify a Wiki snapshot using only copied JSON bytes; never opens SQLite."""

    if not package_dir.is_absolute() or not package_dir.is_dir() or package_dir.is_symlink():
        raise WikiStorePathError("snapshot directory must be an absolute real directory")
    entries = tuple(package_dir.iterdir())
    if tuple(sorted(item.name for item in entries)) != tuple(sorted(WIKI_SNAPSHOT_FILES)):
        raise WikiPublicationError("snapshot must contain exact canonical file membership")
    for path in entries:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or path.is_symlink():
            raise WikiPublicationError("snapshot entries must be regular files")
    checksums_payload = _strict_json_loads((package_dir / "checksums.json").read_bytes())
    checksums = WikiSnapshotChecksumsV1.model_validate(checksums_payload)
    if (package_dir / "checksums.json").read_bytes() != _canonical_file_bytes(
        checksums.model_dump(mode="json")
    ):
        raise WikiPublicationError("snapshot checksums are not canonical")
    for filename, expected in checksums.checksums:
        if _sha256_bytes((package_dir / filename).read_bytes()) != expected:
            raise WikiPublicationError("snapshot file checksum mismatch")
    manifest_payload = _strict_json_loads((package_dir / "manifest.json").read_bytes())
    manifest = WikiGenerationManifestV1.model_validate(manifest_payload)
    if (package_dir / "manifest.json").read_bytes() != _canonical_file_bytes(
        manifest.model_dump(mode="json")
    ):
        raise WikiPublicationError("snapshot manifest is not canonical")
    raw_lines = (package_dir / "records.jsonl").read_bytes().splitlines(keepends=True)
    if not raw_lines:
        raise WikiPublicationError("snapshot records cannot be empty")
    record_digests: list[str] = []
    indexes: list[WikiRecordIndexV1] = []
    for raw_line in raw_lines:
        payload = _strict_json_loads(raw_line)
        if raw_line != _canonical_file_bytes(payload):
            raise WikiPublicationError("snapshot record line is not canonical")
        if not isinstance(payload, dict) or set(payload) != {"index", "record"}:
            raise WikiPublicationError("snapshot record envelope is malformed")
        index = WikiRecordIndexV1.model_validate(payload["index"])
        record = _record_from_payload(index.record_kind, payload["record"])
        if (
            record.logical_path != index.logical_path
            or record.scope.visibility_partition != index.visibility_partition
            or record.content_sha256 != index.record_sha256
        ):
            raise WikiPublicationError("snapshot record envelope cross-file identity mismatch")
        indexes.append(index)
        record_digests.append(record.content_sha256)
    if tuple(indexes) != manifest.records:
        raise WikiPublicationError("snapshot records differ from manifest membership")
    snapshot_payload = _strict_json_loads((package_dir / "snapshot.json").read_bytes())
    snapshot = WikiSnapshotDescriptionV1.model_validate(snapshot_payload)
    if (package_dir / "snapshot.json").read_bytes() != _canonical_file_bytes(
        snapshot.model_dump(mode="json")
    ):
        raise WikiPublicationError("snapshot description is not canonical")
    if (
        snapshot.project_id != manifest.project_id
        or snapshot.generation_id != manifest.generation_id
        or snapshot.manifest_sha256 != manifest.content_sha256
        or snapshot.ordered_record_sha256 != tuple(record_digests)
    ):
        raise WikiPublicationError("snapshot cross-file authority mismatch")
    return WikiSnapshotVerificationV1(
        project_id=snapshot.project_id,
        generation_id=snapshot.generation_id,
        manifest_sha256=manifest.content_sha256,
        record_count=len(record_digests),
        package_set_sha256=checksums.package_set_sha256,
    )
