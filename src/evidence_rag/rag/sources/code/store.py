"""SQLite persistence boundary for Code Source V2 retrieval artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

_FTS_FIELDS = frozenset({"qualified_name", "signature", "path", "identifiers", "doc", "body"})
_SEARCH_TOKEN_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[\u3400-\u9fff]+",
    re.UNICODE,
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: Any, *, default: Any) -> str:
    if value is None:
        value = default
    if isinstance(value, str):
        value = json.loads(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_value(record: Mapping[str, Any], field: str, *, default: Any) -> str:
    if field in record:
        return _canonical_json(record[field], default=default)
    return _canonical_json(record.get(f"{field}_json"), default=default)


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        return " ".join(str(item) for item in value)
    return str(value)


def _required_text(record: Mapping[str, Any], *fields: str) -> str:
    for field in fields:
        value = record.get(field)
        if value is not None and str(value):
            return str(value)
    raise KeyError(fields[0])


def _decode_json_columns(row: sqlite3.Row, fields: Sequence[str]) -> dict[str, Any]:
    item = dict(row)
    for field in fields:
        raw = item.pop(f"{field}_json", None)
        item[field] = json.loads(raw) if raw is not None else None
    return item


def _checked_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    return min(limit, 10_000)


class CodeV2StoreMixin:
    """Store methods mixed into :class:`SQLiteStore`.

    Records deliberately remain ordinary dictionaries so parser and index builder
    contracts can evolve independently from this persistence boundary.
    """

    def insert_code_units(self, records: Iterable[Mapping[str, Any]]) -> int:
        return self._write_code_units(records, upsert=False)

    def upsert_code_units(self, records: Iterable[Mapping[str, Any]]) -> int:
        return self._write_code_units(records, upsert=True)

    def _write_code_units(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        upsert: bool,
    ) -> int:
        now = _utc_now()
        rows = []
        for record in records:
            content = str(record["content"])
            rows.append(
                (
                    str(record["id"]),
                    str(record["entity_id"]),
                    record.get("parent_unit_id"),
                    str(record["repository_id"]),
                    str(record["generation_id"]),
                    str(record["project_id"]),
                    str(record["unit_type"]),
                    record.get("ast_node_type"),
                    int(record.get("ordinal", 0)),
                    _text_value(record.get("language")),
                    _text_value(record.get("path")),
                    _text_value(record.get("qualified_name")),
                    _text_value(record.get("signature")),
                    _text_value(record.get("identifiers")),
                    _text_value(record.get("doc")),
                    _text_value(record.get("body", content)),
                    content,
                    _json_value(record, "context_ref", default={}),
                    record.get("start_line"),
                    record.get("end_line"),
                    record.get("start_byte"),
                    record.get("end_byte"),
                    int(record.get("token_count", 0)),
                    str(record["content_hash"]),
                    str(record["builder_version"]),
                    str(record.get("quality_status", "ready")),
                    str(record["acl_ref"]),
                    _json_value(record, "metadata", default={}),
                    str(record.get("created_at") or now),
                    str(record.get("updated_at") or now),
                )
            )
        if not rows:
            return 0

        conflict = ""
        if upsert:
            conflict = """
            ON CONFLICT(id, generation_id) DO UPDATE SET
                entity_id=excluded.entity_id,
                parent_unit_id=excluded.parent_unit_id,
                repository_id=excluded.repository_id,
                project_id=excluded.project_id,
                unit_type=excluded.unit_type,
                ast_node_type=excluded.ast_node_type,
                ordinal=excluded.ordinal,
                language=excluded.language,
                path=excluded.path,
                qualified_name=excluded.qualified_name,
                signature=excluded.signature,
                identifiers=excluded.identifiers,
                doc=excluded.doc,
                body=excluded.body,
                content=excluded.content,
                context_ref_json=excluded.context_ref_json,
                start_line=excluded.start_line,
                end_line=excluded.end_line,
                start_byte=excluded.start_byte,
                end_byte=excluded.end_byte,
                token_count=excluded.token_count,
                content_hash=excluded.content_hash,
                builder_version=excluded.builder_version,
                quality_status=excluded.quality_status,
                acl_ref=excluded.acl_ref,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """
        with self.transaction() as db:
            db.execute("PRAGMA defer_foreign_keys = ON")
            db.executemany(
                f"""
                INSERT INTO code_retrieval_units(
                    id, entity_id, parent_unit_id, repository_id, generation_id,
                    project_id, unit_type, ast_node_type, ordinal, language, path,
                    qualified_name, signature, identifiers, doc, body, content,
                    context_ref_json, start_line, end_line, start_byte, end_byte,
                    token_count, content_hash, builder_version, quality_status,
                    acl_ref, metadata_json, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                {conflict}
                """,
                rows,
            )
        return len(rows)

    def insert_code_unit_vectors(self, records: Iterable[Mapping[str, Any]]) -> int:
        return self._write_code_unit_vectors(records, upsert=False)

    def upsert_code_unit_vectors(self, records: Iterable[Mapping[str, Any]]) -> int:
        return self._write_code_unit_vectors(records, upsert=True)

    def _write_code_unit_vectors(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        upsert: bool,
    ) -> int:
        now = _utc_now()
        rows = [
            (
                str(record["unit_id"]),
                str(record["generation_id"]),
                str(record["profile"]),
                str(record["model"]),
                int(record["dimension"]),
                bytes(record["vector"]),
                str(record["content_hash"]),
                str(record.get("created_at") or now),
                str(record.get("updated_at") or now),
            )
            for record in records
        ]
        if not rows:
            return 0
        conflict = ""
        if upsert:
            conflict = """
            ON CONFLICT(unit_id, generation_id, profile, model) DO UPDATE SET
                dimension=excluded.dimension,
                vector=excluded.vector,
                content_hash=excluded.content_hash,
                updated_at=excluded.updated_at
            """
        with self.transaction() as db:
            db.executemany(
                f"""
                INSERT INTO code_unit_vectors(
                    unit_id, generation_id, profile, model, dimension, vector,
                    content_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                {conflict}
                """,
                rows,
            )
        return len(rows)

    def upsert_code_embedding_cache(self, records: Iterable[Mapping[str, Any]]) -> int:
        now = _utc_now()
        rows = [
            (
                str(record["cache_key"]),
                str(record["profile"]),
                str(record["model"]),
                int(record["dimension"]),
                bytes(record["vector"]),
                str(record.get("created_at") or now),
                str(record.get("last_used_at") or now),
            )
            for record in records
        ]
        if not rows:
            return 0
        with self.transaction() as db:
            db.executemany(
                """
                INSERT INTO code_unit_embedding_cache(
                    cache_key, profile, model, dimension, vector, created_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key, profile, model) DO UPDATE SET
                    dimension=excluded.dimension,
                    vector=excluded.vector,
                    last_used_at=excluded.last_used_at
                """,
                rows,
            )
        return len(rows)

    def get_code_embedding_cache(
        self,
        cache_key: str,
        *,
        profile: str,
        model: str,
        touch: bool = False,
    ) -> dict[str, Any] | None:
        context = self.transaction() if touch else self.connection()
        with context as db:
            row = db.execute(
                """
                SELECT * FROM code_unit_embedding_cache
                WHERE cache_key=? AND profile=? AND model=?
                """,
                (cache_key, profile, model),
            ).fetchone()
            if row is not None and touch:
                touched = _utc_now()
                db.execute(
                    """
                    UPDATE code_unit_embedding_cache SET last_used_at=?
                    WHERE cache_key=? AND profile=? AND model=?
                    """,
                    (touched, cache_key, profile, model),
                )
                item = dict(row)
                item["last_used_at"] = touched
                return item
        return dict(row) if row is not None else None

    def insert_code_relation_diagnostics(
        self,
        records: Iterable[Mapping[str, Any]],
    ) -> int:
        return self._write_code_relation_diagnostics(records, upsert=False)

    def upsert_code_relation_diagnostics(
        self,
        records: Iterable[Mapping[str, Any]],
    ) -> int:
        return self._write_code_relation_diagnostics(records, upsert=True)

    def _write_code_relation_diagnostics(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        upsert: bool,
    ) -> int:
        now = _utc_now()
        rows = []
        for record in records:
            candidates_json = _json_value(record, "candidates", default=[])
            diagnostic_id = record.get("id")
            if diagnostic_id is None:
                identity = _canonical_json(
                    {
                        "generation_id": record["generation_id"],
                        "parser": record["parser"],
                        "raw_target": record.get("raw_target", ""),
                        "relation_type": record["relation_type"],
                        "source_entity_id": record["source_entity_id"],
                    },
                    default={},
                )
                diagnostic_id = f"code-diagnostic://{hashlib.sha256(identity.encode()).hexdigest()}"
            rows.append(
                (
                    str(diagnostic_id),
                    str(record["repository_id"]),
                    str(record["generation_id"]),
                    str(record["project_id"]),
                    str(record["source_entity_id"]),
                    str(record["relation_type"]),
                    str(record.get("raw_target", "")),
                    str(record["reason"]),
                    candidates_json,
                    str(record["parser"]),
                    str(record["acl_ref"]),
                    str(record.get("created_at") or now),
                    str(record.get("updated_at") or now),
                )
            )
        if not rows:
            return 0
        conflict = ""
        if upsert:
            conflict = """
            ON CONFLICT(id, generation_id) DO UPDATE SET
                repository_id=excluded.repository_id,
                project_id=excluded.project_id,
                source_entity_id=excluded.source_entity_id,
                relation_type=excluded.relation_type,
                raw_target=excluded.raw_target,
                reason=excluded.reason,
                candidates_json=excluded.candidates_json,
                parser=excluded.parser,
                acl_ref=excluded.acl_ref,
                updated_at=excluded.updated_at
            """
        with self.transaction() as db:
            db.executemany(
                f"""
                INSERT INTO code_relation_diagnostics(
                    id, repository_id, generation_id, project_id, source_entity_id,
                    relation_type, raw_target, reason, candidates_json, parser,
                    acl_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                {conflict}
                """,
                rows,
            )
        return len(rows)

    def exact_code_unit_lookup(
        self,
        query: str | None = None,
        *,
        unit_id: str | None = None,
        qualified_name: str | None = None,
        signature: str | None = None,
        path: str | None = None,
        project_id: str | None = None,
        repository_ids: Sequence[str] | None = None,
        generation_id: str | None = None,
        allowed_acl_refs: Sequence[str] | None = None,
        active_only: bool = True,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        checked_limit = _checked_limit(limit)
        clauses, values = self._code_unit_scope(
            project_id=project_id,
            repository_ids=repository_ids,
            generation_id=generation_id,
            allowed_acl_refs=allowed_acl_refs,
            active_only=active_only,
        )
        exact_clauses: list[str] = []
        if query is not None:
            exact_clauses.append("(u.id=? OR u.qualified_name=? OR u.signature=? OR u.path=?)")
            values.extend([query, query, query, query])
        for column, value in (
            ("id", unit_id),
            ("qualified_name", qualified_name),
            ("signature", signature),
            ("path", path),
        ):
            if value is not None:
                exact_clauses.append(f"u.{column}=?")
                values.append(value)
        if not exact_clauses:
            raise ValueError("an exact unit identity or field is required")
        clauses.extend(exact_clauses)
        values.append(checked_limit)
        with self.connection() as db:
            rows = db.execute(
                f"""
                SELECT u.* FROM code_retrieval_units u
                JOIN repositories r ON r.id=u.repository_id
                WHERE {" AND ".join(clauses)}
                ORDER BY u.repository_id, u.path, u.start_line, u.ordinal, u.id
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._decode_code_unit(row) for row in rows]

    def lookup_code_units_exact(
        self,
        query: str | None = None,
        **scope: Any,
    ) -> list[dict[str, Any]]:
        return self.exact_code_unit_lookup(query, **scope)

    def search_code_units_sparse(
        self,
        query: str | Mapping[str, str],
        *,
        fields: Mapping[str, str] | None = None,
        project_id: str | None = None,
        repository_ids: Sequence[str] | None = None,
        generation_id: str | None = None,
        allowed_acl_refs: Sequence[str] | None = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        checked_limit = _checked_limit(limit)
        if isinstance(query, Mapping):
            if fields is not None:
                raise ValueError("fielded query must be supplied only once")
            fields = query
            query = ""
        fts_query = self._code_fts_query(query, fields)
        if not fts_query:
            return []
        clauses, values = self._code_unit_scope(
            project_id=project_id,
            repository_ids=repository_ids,
            generation_id=generation_id,
            allowed_acl_refs=allowed_acl_refs,
            active_only=active_only,
        )
        values = [fts_query, *values, checked_limit]
        with self.connection() as db:
            rows = db.execute(
                f"""
                SELECT u.*,
                       bm25(
                           code_retrieval_units_fts, 10.0, 8.0, 6.0, 5.0, 2.0, 1.0
                       ) AS sparse_rank
                FROM code_retrieval_units_fts
                JOIN code_retrieval_units u
                  ON u.rowid=code_retrieval_units_fts.rowid
                JOIN repositories r ON r.id=u.repository_id
                WHERE code_retrieval_units_fts MATCH ?
                  AND {" AND ".join(clauses)}
                ORDER BY sparse_rank, u.repository_id, u.path, u.start_line, u.ordinal
                LIMIT ?
                """,
                values,
            ).fetchall()
        return [self._decode_code_unit(row) for row in rows]

    def fielded_code_unit_search(
        self,
        query: str | Mapping[str, str],
        **scope: Any,
    ) -> list[dict[str, Any]]:
        return self.search_code_units_sparse(query, **scope)

    def iter_code_unit_vectors(
        self,
        *,
        project_id: str | None = None,
        repository_ids: Sequence[str] | None = None,
        generation_id: str | None = None,
        allowed_acl_refs: Sequence[str] | None = None,
        profile: str | None = None,
        model: str | None = None,
        active_only: bool = True,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        checked_limit = _checked_limit(limit)
        clauses, values = self._code_unit_scope(
            project_id=project_id,
            repository_ids=repository_ids,
            generation_id=generation_id,
            allowed_acl_refs=allowed_acl_refs,
            active_only=active_only,
        )
        if profile is not None:
            clauses.append("v.profile=?")
            values.append(profile)
        if model is not None:
            clauses.append("v.model=?")
            values.append(model)
        limit_sql = ""
        if checked_limit is not None:
            limit_sql = "LIMIT ?"
            values.append(checked_limit)
        sql = f"""
            SELECT v.*, u.entity_id, u.repository_id, u.project_id, u.unit_type,
                   u.language, u.path, u.qualified_name, u.acl_ref, u.metadata_json
            FROM code_unit_vectors v
            JOIN code_retrieval_units u
              ON u.id=v.unit_id AND u.generation_id=v.generation_id
            JOIN repositories r ON r.id=u.repository_id
            WHERE {" AND ".join(clauses)}
            ORDER BY u.repository_id, v.generation_id, v.unit_id, v.profile, v.model
            {limit_sql}
        """
        with self.connection() as db:
            cursor = db.execute(sql, values)
            while rows := cursor.fetchmany(256):
                for row in rows:
                    item = _decode_json_columns(row, ("metadata",))
                    yield item

    def dense_code_unit_candidates(self, **scope: Any) -> Iterator[dict[str, Any]]:
        return self.iter_code_unit_vectors(**scope)

    def load_code_unit(
        self,
        unit_id: str,
        *,
        project_id: str | None = None,
        repository_id: str | None = None,
        generation_id: str | None = None,
        allowed_acl_refs: Sequence[str] | None = None,
        active_only: bool = True,
    ) -> dict[str, Any] | None:
        rows = self.exact_code_unit_lookup(
            unit_id=unit_id,
            project_id=project_id,
            repository_ids=[repository_id] if repository_id is not None else None,
            generation_id=generation_id,
            allowed_acl_refs=allowed_acl_refs,
            active_only=active_only,
            limit=1,
        )
        return rows[0] if rows else None

    def load_code_unit_context(
        self,
        unit_id: str,
        *,
        project_id: str | None = None,
        repository_id: str | None = None,
        generation_id: str | None = None,
        allowed_acl_refs: Sequence[str] | None = None,
        active_only: bool = True,
        child_limit: int = 50,
    ) -> dict[str, Any] | None:
        unit = self.load_code_unit(
            unit_id,
            project_id=project_id,
            repository_id=repository_id,
            generation_id=generation_id,
            allowed_acl_refs=allowed_acl_refs,
            active_only=active_only,
        )
        if unit is None:
            return None
        checked_limit = _checked_limit(child_limit)
        with self.connection() as db:
            entity = db.execute(
                """
                SELECT * FROM entities
                WHERE id=? AND generation_id=? AND repository_id=?
                  AND project_id=? AND acl_ref=?
                LIMIT 1
                """,
                (
                    unit["entity_id"],
                    unit["generation_id"],
                    unit["repository_id"],
                    unit["project_id"],
                    unit["acl_ref"],
                ),
            ).fetchone()
            parent = None
            if unit["parent_unit_id"]:
                parent = db.execute(
                    """
                    SELECT * FROM code_retrieval_units
                    WHERE id=? AND generation_id=? AND repository_id=?
                      AND project_id=? AND acl_ref=?
                    LIMIT 1
                    """,
                    (
                        unit["parent_unit_id"],
                        unit["generation_id"],
                        unit["repository_id"],
                        unit["project_id"],
                        unit["acl_ref"],
                    ),
                ).fetchone()
            children = db.execute(
                """
                SELECT * FROM code_retrieval_units
                WHERE parent_unit_id=? AND generation_id=? AND repository_id=?
                  AND project_id=? AND acl_ref=?
                ORDER BY ordinal, start_byte, id
                LIMIT ?
                """,
                (
                    unit["id"],
                    unit["generation_id"],
                    unit["repository_id"],
                    unit["project_id"],
                    unit["acl_ref"],
                    checked_limit,
                ),
            ).fetchall()
        entity_item = _decode_json_columns(entity, ("metadata",)) if entity else None
        return {
            "unit": unit,
            "entity": entity_item,
            "parent": self._decode_code_unit(parent) if parent else None,
            "children": [self._decode_code_unit(row) for row in children],
            "context_ref": unit["context_ref"],
        }

    def load_code_context(self, unit_id: str, **scope: Any) -> dict[str, Any] | None:
        return self.load_code_unit_context(unit_id, **scope)

    def upsert_code_retrieval_calibrations(
        self,
        records: Iterable[Mapping[str, Any]],
    ) -> int:
        now = _utc_now()
        rows = [
            (
                str(record["id"]),
                str(record["profile"]),
                str(record["task"]),
                str(record["entity_type"]),
                str(record["model"]),
                str(record["artifact_ref"]),
                _json_value(record, "metrics", default={}),
                str(record["status"]),
                str(record.get("created_at") or now),
                str(record.get("updated_at") or now),
            )
            for record in records
        ]
        if not rows:
            return 0
        with self.transaction() as db:
            db.executemany(
                """
                INSERT INTO code_retrieval_calibrations(
                    id, profile, task, entity_type, model, artifact_ref, metrics_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    profile=excluded.profile,
                    task=excluded.task,
                    entity_type=excluded.entity_type,
                    model=excluded.model,
                    artifact_ref=excluded.artifact_ref,
                    metrics_json=excluded.metrics_json,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                rows,
            )
        return len(rows)

    def get_code_retrieval_calibration(
        self,
        calibration_id: str,
    ) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM code_retrieval_calibrations WHERE id=?",
                (calibration_id,),
            ).fetchone()
        return _decode_json_columns(row, ("metrics",)) if row else None

    def upsert_code_index_publication(
        self,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        now = _utc_now()
        generation_id = str(record["generation_id"])
        repository_id = str(record["repository_id"])
        project_id = str(record["project_id"])
        with self.transaction() as db:
            owner = db.execute(
                "SELECT project_id FROM repositories WHERE id=?",
                (repository_id,),
            ).fetchone()
            if owner is None or str(owner["project_id"]) != project_id:
                raise ValueError("code index publication repository/project mismatch")
            db.execute(
                """
                INSERT INTO code_index_publications(
                    generation_id, repository_id, project_id, builder, sparse,
                    embedding, graph, status, validation_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(generation_id) DO UPDATE SET
                    repository_id=excluded.repository_id,
                    project_id=excluded.project_id,
                    builder=excluded.builder,
                    sparse=excluded.sparse,
                    embedding=excluded.embedding,
                    graph=excluded.graph,
                    status=excluded.status,
                    validation_json=excluded.validation_json,
                    updated_at=excluded.updated_at
                """,
                (
                    generation_id,
                    repository_id,
                    project_id,
                    _required_text(record, "builder", "builder_version"),
                    _required_text(record, "sparse", "sparse_version"),
                    _required_text(record, "embedding", "embedding_profile"),
                    _required_text(record, "graph", "graph_version"),
                    str(record["status"]),
                    _json_value(record, "validation", default={}),
                    str(record.get("created_at") or now),
                    str(record.get("updated_at") or now),
                ),
            )
        publication = self.get_code_index_publication(
            generation_id,
            repository_id=repository_id,
            active_only=False,
        )
        if publication is None:
            raise RuntimeError("code index publication disappeared after upsert")
        return publication

    def get_code_index_publication(
        self,
        generation_id: str,
        *,
        repository_id: str | None = None,
        active_only: bool = False,
    ) -> dict[str, Any] | None:
        clauses = ["p.generation_id=?"]
        values: list[Any] = [generation_id]
        if repository_id is not None:
            clauses.append("p.repository_id=?")
            values.append(repository_id)
        if active_only:
            clauses.append("p.generation_id=r.active_generation_id")
        with self.connection() as db:
            row = db.execute(
                f"""
                SELECT p.* FROM code_index_publications p
                JOIN repositories r ON r.id=p.repository_id
                WHERE {" AND ".join(clauses)}
                LIMIT 1
                """,
                values,
            ).fetchone()
        return _decode_json_columns(row, ("validation",)) if row else None

    def active_code_generations(
        self,
        *,
        project_id: str | None = None,
        repository_ids: Sequence[str] | None = None,
    ) -> dict[str, str]:
        clauses = ["active_generation_id IS NOT NULL"]
        values: list[Any] = []
        if project_id is not None:
            clauses.append("project_id=?")
            values.append(project_id)
        if repository_ids is not None:
            if not repository_ids:
                return {}
            marks = ",".join("?" for _ in repository_ids)
            clauses.append(f"id IN ({marks})")
            values.extend(repository_ids)
        with self.connection() as db:
            rows = db.execute(
                f"""
                SELECT id, active_generation_id FROM repositories
                WHERE {" AND ".join(clauses)}
                ORDER BY id
                """,
                values,
            ).fetchall()
        return {str(row["id"]): str(row["active_generation_id"]) for row in rows}

    def cleanup_code_generation(
        self,
        generation_id: str,
        *,
        repository_id: str | None = None,
    ) -> dict[str, int]:
        clauses = ["generation_id=?"]
        values: list[Any] = [generation_id]
        if repository_id is not None:
            clauses.append("repository_id=?")
            values.append(repository_id)
        where = " AND ".join(clauses)
        with self.transaction() as db:
            counts = {
                "units": int(
                    db.execute(
                        f"SELECT count(*) FROM code_retrieval_units WHERE {where}",
                        values,
                    ).fetchone()[0]
                ),
                "vectors": int(
                    db.execute(
                        """
                        SELECT count(*) FROM code_unit_vectors v
                        JOIN code_retrieval_units u
                          ON u.id=v.unit_id AND u.generation_id=v.generation_id
                        WHERE """
                        + " AND ".join(f"u.{clause}" for clause in clauses),
                        values,
                    ).fetchone()[0]
                ),
                "diagnostics": int(
                    db.execute(
                        f"SELECT count(*) FROM code_relation_diagnostics WHERE {where}",
                        values,
                    ).fetchone()[0]
                ),
                "publications": int(
                    db.execute(
                        f"SELECT count(*) FROM code_index_publications WHERE {where}",
                        values,
                    ).fetchone()[0]
                ),
            }
            db.execute(f"DELETE FROM code_relation_diagnostics WHERE {where}", values)
            db.execute(f"DELETE FROM code_index_publications WHERE {where}", values)
            db.execute(f"DELETE FROM code_retrieval_units WHERE {where}", values)
        return counts

    def code_v2_integrity_stats(
        self,
        *,
        repository_id: str | None = None,
        generation_id: str | None = None,
    ) -> dict[str, Any]:
        unit_clauses = ["1=1"]
        unit_values: list[Any] = []
        if repository_id is not None:
            unit_clauses.append("u.repository_id=?")
            unit_values.append(repository_id)
        if generation_id is not None:
            unit_clauses.append("u.generation_id=?")
            unit_values.append(generation_id)
        unit_where = " AND ".join(unit_clauses)
        with self.connection() as db:
            units = int(
                db.execute(
                    f"SELECT count(*) FROM code_retrieval_units u WHERE {unit_where}",
                    unit_values,
                ).fetchone()[0]
            )
            vectors = int(
                db.execute(
                    f"""
                    SELECT count(*) FROM code_unit_vectors v
                    JOIN code_retrieval_units u
                      ON u.id=v.unit_id AND u.generation_id=v.generation_id
                    WHERE {unit_where}
                    """,
                    unit_values,
                ).fetchone()[0]
            )
            active_units = int(
                db.execute(
                    f"""
                    SELECT count(*) FROM code_retrieval_units u
                    JOIN repositories r ON r.id=u.repository_id
                    WHERE {unit_where} AND u.generation_id=r.active_generation_id
                    """,
                    unit_values,
                ).fetchone()[0]
            )
            missing_vectors = int(
                db.execute(
                    f"""
                    SELECT count(*) FROM code_retrieval_units u
                    WHERE {unit_where}
                      AND NOT EXISTS (
                        SELECT 1 FROM code_unit_vectors v
                        WHERE v.unit_id=u.id AND v.generation_id=u.generation_id
                      )
                    """,
                    unit_values,
                ).fetchone()[0]
            )
            vector_hash_mismatches = int(
                db.execute(
                    f"""
                    SELECT count(*) FROM code_unit_vectors v
                    JOIN code_retrieval_units u
                      ON u.id=v.unit_id AND u.generation_id=v.generation_id
                    WHERE {unit_where} AND v.content_hash<>u.content_hash
                    """,
                    unit_values,
                ).fetchone()[0]
            )
            identity_mismatches = int(
                db.execute(
                    f"""
                    SELECT count(*) FROM code_retrieval_units u
                    JOIN entities e
                      ON e.id=u.entity_id AND e.generation_id=u.generation_id
                    WHERE {unit_where}
                      AND (
                        e.repository_id<>u.repository_id
                        OR e.project_id<>u.project_id
                        OR e.acl_ref<>u.acl_ref
                      )
                    """,
                    unit_values,
                ).fetchone()[0]
            )
            indexed_units = int(
                db.execute(
                    f"""
                    SELECT count(*) FROM code_retrieval_units u
                    JOIN code_retrieval_units_fts_docsize f ON f.id=u.rowid
                    WHERE {unit_where}
                    """,
                    unit_values,
                ).fetchone()[0]
            )
            code_tables = {
                "code_retrieval_units",
                "code_unit_vectors",
                "code_relation_diagnostics",
                "code_index_publications",
            }
            foreign_violations = [
                tuple(row)
                for row in db.execute("PRAGMA foreign_key_check").fetchall()
                if str(row[0]) in code_tables
            ]
            diagnostics = int(
                db.execute(
                    """
                    SELECT count(*) FROM code_relation_diagnostics
                    WHERE (? IS NULL OR repository_id=?)
                      AND (? IS NULL OR generation_id=?)
                    """,
                    (repository_id, repository_id, generation_id, generation_id),
                ).fetchone()[0]
            )
            publications = int(
                db.execute(
                    """
                    SELECT count(*) FROM code_index_publications
                    WHERE (? IS NULL OR repository_id=?)
                      AND (? IS NULL OR generation_id=?)
                    """,
                    (repository_id, repository_id, generation_id, generation_id),
                ).fetchone()[0]
            )
        violations = {
            "foreign_keys": len(foreign_violations),
            "identity": identity_mismatches,
            "vector_content_hash": vector_hash_mismatches,
            "fts_index": max(0, units - indexed_units),
        }
        return {
            "units": units,
            "active_units": active_units,
            "vectors": vectors,
            "units_without_vectors": missing_vectors,
            "diagnostics": diagnostics,
            "publications": publications,
            "fts_indexed_units": indexed_units,
            "violations": violations,
            "healthy": not any(violations.values()),
        }

    def code_integrity_stats(self, **scope: Any) -> dict[str, Any]:
        return self.code_v2_integrity_stats(**scope)

    def _code_unit_scope(
        self,
        *,
        project_id: str | None,
        repository_ids: Sequence[str] | None,
        generation_id: str | None,
        allowed_acl_refs: Sequence[str] | None,
        active_only: bool,
    ) -> tuple[list[str], list[Any]]:
        clauses = ["1=1"]
        values: list[Any] = []
        if active_only:
            clauses.append("u.generation_id=r.active_generation_id")
        if project_id is not None:
            clauses.append("u.project_id=?")
            values.append(project_id)
        if repository_ids is not None:
            if not repository_ids:
                clauses.append("0=1")
            else:
                marks = ",".join("?" for _ in repository_ids)
                clauses.append(f"u.repository_id IN ({marks})")
                values.extend(repository_ids)
        if generation_id is not None:
            clauses.append("u.generation_id=?")
            values.append(generation_id)
        if allowed_acl_refs is not None:
            allowed = list(dict.fromkeys([*allowed_acl_refs, "public"]))
            marks = ",".join("?" for _ in allowed)
            clauses.append(f"u.acl_ref IN ({marks})")
            values.extend(allowed)
        return clauses, values

    def _code_fts_query(
        self,
        query: str,
        fields: Mapping[str, str] | None,
    ) -> str:
        groups: list[str] = []
        tokens = _SEARCH_TOKEN_RE.findall(query)
        if tokens:
            groups.append("(" + " OR ".join(self._quote_fts_token(token) for token in tokens) + ")")
        for field, text in (fields or {}).items():
            if field not in _FTS_FIELDS:
                raise ValueError(f"unsupported Code FTS field: {field}")
            field_tokens = _SEARCH_TOKEN_RE.findall(text)
            if not field_tokens:
                continue
            field_query = " OR ".join(
                f"{field}:{self._quote_fts_token(token)}" for token in field_tokens
            )
            groups.append(f"({field_query})")
        return " AND ".join(groups)

    @staticmethod
    def _quote_fts_token(token: str) -> str:
        return f'"{token.replace(chr(34), chr(34) * 2)}"'

    @staticmethod
    def _decode_code_unit(row: sqlite3.Row) -> dict[str, Any]:
        return _decode_json_columns(row, ("context_ref", "metadata"))


__all__ = ["CodeV2StoreMixin"]
