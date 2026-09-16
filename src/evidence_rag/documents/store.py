from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA


def _decode(row: sqlite3.Row | None, *fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for field in fields:
        item[field] = json.loads(item.pop(f"{field}_json") or "{}")
    return item


class DocumentStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)

    def create_document(
        self,
        document: dict[str, Any],
        sections: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        pages: list[dict[str, Any]] | None = None,
        tables: list[dict[str, Any]] | None = None,
        figures: list[dict[str, Any]] | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        pages = pages or []
        tables = tables or []
        figures = figures or []
        citations = citations or []
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO scientific_documents
                   (id, display_key, project_id, iteration_id, title, version, source_type,
                    source_uri, content_hash, content, authors_json, tags_json, status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed', ?, ?)""",
                (
                    document["id"],
                    document["display_key"],
                    document["project_id"],
                    document.get("iteration_id"),
                    document["title"],
                    document["version"],
                    document["source_type"],
                    document["source_uri"],
                    document["content_hash"],
                    document["content"],
                    json.dumps(document["authors"], ensure_ascii=False),
                    json.dumps(document["tags"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            db.executemany(
                """INSERT INTO document_sections
                   (id, document_id, project_id, ordinal, level, title, content,
                    start_line, end_line, source_locator, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"],
                        document["id"],
                        document["project_id"],
                        item["ordinal"],
                        item["level"],
                        item["title"],
                        item["content"],
                        item["start_line"],
                        item["end_line"],
                        item["source_locator"],
                        now,
                    )
                    for item in sections
                ],
            )
            db.executemany(
                """INSERT INTO document_pages
                   (id, document_id, project_id, page_number, content,
                    source_locator, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"],
                        document["id"],
                        document["project_id"],
                        item["page_number"],
                        item["content"],
                        item["source_locator"],
                        json.dumps(item.get("metadata", {}), ensure_ascii=False),
                        now,
                    )
                    for item in pages
                ],
            )
            for table in tables:
                db.execute(
                    """INSERT INTO document_tables
                       (id, document_id, project_id, section_id, page_number, ordinal,
                        title, caption, row_count, column_count, extraction_confidence,
                        source_locator, metadata_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        table["id"],
                        document["id"],
                        document["project_id"],
                        table.get("section_id"),
                        table.get("page_number"),
                        table["ordinal"],
                        table.get("title", ""),
                        table.get("caption", ""),
                        len(table.get("rows", [])),
                        max((len(row) for row in table.get("rows", [])), default=0),
                        table.get("extraction_confidence", 1.0),
                        table["source_locator"],
                        json.dumps(table.get("metadata", {}), ensure_ascii=False),
                        now,
                    ),
                )
                cells = []
                for row_index, row in enumerate(table.get("rows", [])):
                    for column_index, value in enumerate(row):
                        cells.append(
                            (
                                f"{table['id']}/cell/{row_index}/{column_index}",
                                table["id"],
                                document["id"],
                                document["project_id"],
                                row_index,
                                column_index,
                                str(value),
                                int(row_index == 0),
                                f"{table['source_locator']}#cell={row_index},{column_index}",
                                now,
                            )
                        )
                db.executemany(
                    """INSERT INTO document_table_cells
                       (id, table_id, document_id, project_id, row_index, column_index,
                        value, is_header, source_locator, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    cells,
                )
            db.executemany(
                """INSERT INTO document_figures
                   (id, document_id, project_id, page_number, ordinal, caption,
                    source_locator, artifact_ref, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"],
                        document["id"],
                        document["project_id"],
                        item.get("page_number"),
                        item["ordinal"],
                        item.get("caption", ""),
                        item["source_locator"],
                        item.get("artifact_ref"),
                        json.dumps(item.get("metadata", {}), ensure_ascii=False),
                        now,
                    )
                    for item in figures
                ],
            )
            db.executemany(
                """INSERT INTO document_citations
                   (id, document_id, project_id, marker, context, target,
                    source_locator, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"],
                        document["id"],
                        document["project_id"],
                        item["marker"],
                        item["context"],
                        item.get("target"),
                        item["source_locator"],
                        json.dumps(item.get("metadata", {}), ensure_ascii=False),
                        now,
                    )
                    for item in citations
                ],
            )
            db.executemany(
                """INSERT INTO claims
                   (id, display_key, project_id, document_id, section_id, content, claim_type,
                    status, extraction_method, extraction_confidence, source_locator,
                    metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'reported', ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"],
                        item["display_key"],
                        document["project_id"],
                        document["id"],
                        item.get("section_id"),
                        item["content"],
                        item["claim_type"],
                        item["extraction_method"],
                        item["extraction_confidence"],
                        item["source_locator"],
                        json.dumps(item.get("metadata", {}), ensure_ascii=False),
                        now,
                        now,
                    )
                    for item in claims
                ],
            )
        return self.get_document(document["id"]) or {}

    def find_version(self, project_id: str, title: str, version: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT id FROM scientific_documents
                   WHERE project_id=? AND title=? AND version=?""",
                (project_id, title, version),
            ).fetchone()
        return self.get_document(row["id"]) if row else None

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                "SELECT * FROM scientific_documents WHERE id=?", (document_id,)
            ).fetchone()
            if not row:
                return None
            sections = db.execute(
                "SELECT * FROM document_sections WHERE document_id=? ORDER BY ordinal",
                (document_id,),
            ).fetchall()
            claims = db.execute(
                "SELECT * FROM claims WHERE document_id=? ORDER BY created_at",
                (document_id,),
            ).fetchall()
            pages = db.execute(
                "SELECT * FROM document_pages WHERE document_id=? ORDER BY page_number",
                (document_id,),
            ).fetchall()
            tables = db.execute(
                "SELECT * FROM document_tables WHERE document_id=? ORDER BY ordinal",
                (document_id,),
            ).fetchall()
            figures = db.execute(
                "SELECT * FROM document_figures WHERE document_id=? ORDER BY ordinal",
                (document_id,),
            ).fetchall()
            citations = db.execute(
                "SELECT * FROM document_citations WHERE document_id=? ORDER BY created_at",
                (document_id,),
            ).fetchall()
        result = _decode(row, "authors", "tags") or {}
        result["sections"] = [dict(item) for item in sections]
        result["claims"] = [_decode(item, "metadata") or {} for item in claims]
        result["pages"] = [_decode(item, "metadata") or {} for item in pages]
        result["tables"] = []
        with self.database.connection() as db:
            for row in tables:
                table = _decode(row, "metadata") or {}
                cells = db.execute(
                    """SELECT * FROM document_table_cells WHERE table_id=?
                       ORDER BY row_index, column_index""",
                    (table["id"],),
                ).fetchall()
                table["cells"] = [dict(item) for item in cells]
                result["tables"].append(table)
        result["figures"] = [_decode(item, "metadata") or {} for item in figures]
        result["citations"] = [_decode(item, "metadata") or {} for item in citations]
        return result

    def upsert_match_candidate(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO claim_match_candidates
                   (id, project_id, claim_id, run_id, score, signals_json,
                    review_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'unreviewed', ?, ?)
                   ON CONFLICT(claim_id, run_id) DO UPDATE SET
                     score=excluded.score, signals_json=excluded.signals_json,
                     updated_at=excluded.updated_at""",
                (
                    record["id"],
                    record["project_id"],
                    record["claim_id"],
                    record["run_id"],
                    record["score"],
                    json.dumps(record.get("signals", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = db.execute(
                """SELECT * FROM claim_match_candidates
                   WHERE claim_id=? AND run_id=?""",
                (record["claim_id"], record["run_id"]),
            ).fetchone()
        return _decode(row, "signals") or {}

    def list_match_candidates(
        self,
        project_id: str,
        claim_id: str | None = None,
        review_status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["m.project_id=?"]
        values: list[Any] = [project_id]
        if claim_id:
            clauses.append("m.claim_id=?")
            values.append(claim_id)
        if review_status:
            clauses.append("m.review_status=?")
            values.append(review_status)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT m.*, r.display_key run_key, r.name run_name,
                           c.display_key claim_key, c.content claim_text
                    FROM claim_match_candidates m
                    JOIN experiment_runs r ON r.id=m.run_id
                    JOIN claims c ON c.id=m.claim_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY m.score DESC, m.updated_at DESC""",
                values,
            ).fetchall()
        return [_decode(row, "signals") or {} for row in rows]

    def get_match_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT m.*, r.display_key run_key, r.name run_name,
                          c.display_key claim_key, c.content claim_text
                   FROM claim_match_candidates m
                   JOIN experiment_runs r ON r.id=m.run_id
                   JOIN claims c ON c.id=m.claim_id WHERE m.id=?""",
                (candidate_id,),
            ).fetchone()
            reviews = db.execute(
                """SELECT * FROM claim_match_reviews WHERE candidate_id=?
                   ORDER BY created_at DESC""",
                (candidate_id,),
            ).fetchall()
        if not row:
            return None
        result = _decode(row, "signals") or {}
        result["reviews"] = [dict(item) for item in reviews]
        return result

    def review_match_candidate(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """UPDATE claim_match_candidates SET review_status=?, updated_at=?
                   WHERE id=?""",
                (record["decision"], now, record["candidate_id"]),
            )
            db.execute(
                """INSERT INTO claim_match_reviews
                   (id, project_id, candidate_id, decision, reviewer, relationship,
                    note, claim_evidence_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["project_id"],
                    record["candidate_id"],
                    record["decision"],
                    record["reviewer"],
                    record["relationship"],
                    record["note"],
                    record.get("claim_evidence_id"),
                    now,
                ),
            )
        return self.get_match_candidate(record["candidate_id"]) or {}

    def upsert_table_metric_candidate(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO table_metric_match_candidates
                   (id, project_id, document_id, claim_id, table_id, table_cell_id,
                    metric_id, run_id, score, signals_json, review_status,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unreviewed', ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     score=excluded.score, signals_json=excluded.signals_json,
                     updated_at=excluded.updated_at""",
                (
                    record["id"],
                    record["project_id"],
                    record["document_id"],
                    record.get("claim_id"),
                    record["table_id"],
                    record["table_cell_id"],
                    record["metric_id"],
                    record["run_id"],
                    record["score"],
                    json.dumps(record.get("signals", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = db.execute(
                "SELECT * FROM table_metric_match_candidates WHERE id=?", (record["id"],)
            ).fetchone()
        return _decode(row, "signals") or {}

    def list_table_metric_candidates(
        self,
        project_id: str,
        *,
        document_id: str | None = None,
        claim_id: str | None = None,
        review_status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["candidate.project_id=?"]
        values: list[Any] = [project_id]
        if document_id:
            clauses.append("candidate.document_id=?")
            values.append(document_id)
        if claim_id:
            clauses.append("candidate.claim_id=?")
            values.append(claim_id)
        if review_status:
            clauses.append("candidate.review_status=?")
            values.append(review_status)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT candidate.*, cell.value cell_value,
                           cell.source_locator cell_locator, table_record.title table_title,
                           metric.name metric_name, metric.value metric_value,
                           metric.unit metric_unit, run.display_key run_key,
                           run.name run_name, claim.display_key claim_key
                    FROM table_metric_match_candidates candidate
                    JOIN document_table_cells cell ON cell.id=candidate.table_cell_id
                    JOIN document_tables table_record ON table_record.id=candidate.table_id
                    JOIN run_metrics metric ON metric.id=candidate.metric_id
                    JOIN experiment_runs run ON run.id=candidate.run_id
                    LEFT JOIN claims claim ON claim.id=candidate.claim_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY candidate.score DESC, candidate.updated_at DESC""",
                values,
            ).fetchall()
        return [_decode(row, "signals") or {} for row in rows]

    def get_table_metric_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT candidate.*, cell.value cell_value,
                          cell.source_locator cell_locator, table_record.title table_title,
                          metric.name metric_name, metric.value metric_value,
                          metric.unit metric_unit, run.display_key run_key,
                          run.name run_name, claim.display_key claim_key
                   FROM table_metric_match_candidates candidate
                   JOIN document_table_cells cell ON cell.id=candidate.table_cell_id
                   JOIN document_tables table_record ON table_record.id=candidate.table_id
                   JOIN run_metrics metric ON metric.id=candidate.metric_id
                   JOIN experiment_runs run ON run.id=candidate.run_id
                   LEFT JOIN claims claim ON claim.id=candidate.claim_id
                   WHERE candidate.id=?""",
                (candidate_id,),
            ).fetchone()
            reviews = db.execute(
                """SELECT * FROM table_metric_match_reviews WHERE candidate_id=?
                   ORDER BY created_at DESC""",
                (candidate_id,),
            ).fetchall()
        if not row:
            return None
        result = _decode(row, "signals") or {}
        result["reviews"] = [dict(item) for item in reviews]
        return result

    def review_table_metric_candidate(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """UPDATE table_metric_match_candidates
                   SET review_status=?, updated_at=? WHERE id=?""",
                (record["decision"], now, record["candidate_id"]),
            )
            db.execute(
                """INSERT INTO table_metric_match_reviews
                   (id, project_id, candidate_id, decision, reviewer, note,
                    claim_evidence_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["project_id"],
                    record["candidate_id"],
                    record["decision"],
                    record["reviewer"],
                    record.get("note", ""),
                    record.get("claim_evidence_id"),
                    now,
                ),
            )
        return self.get_table_metric_candidate(record["candidate_id"]) or {}

    def list_table_contexts(
        self, project_id: str, document_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["table_record.project_id=?"]
        values: list[Any] = [project_id]
        if document_id:
            clauses.append("table_record.document_id=?")
            values.append(document_id)
        with self.database.connection() as db:
            tables = db.execute(
                f"""SELECT table_record.* FROM document_tables table_record
                    WHERE {" AND ".join(clauses)} ORDER BY table_record.created_at""",
                values,
            ).fetchall()
            results = []
            for table_row in tables:
                table = _decode(table_row, "metadata") or {}
                cells = db.execute(
                    """SELECT * FROM document_table_cells WHERE table_id=?
                       ORDER BY row_index, column_index""",
                    (table["id"],),
                ).fetchall()
                table["cells"] = [dict(cell) for cell in cells]
                results.append(table)
        return results

    def list_project_metrics(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT metric.*, run.display_key run_key, run.name run_name,
                          run.external_id, run.dataset_id, run.dataset_version,
                          run.commit_sha
                   FROM run_metrics metric
                   JOIN experiment_runs run ON run.id=metric.run_id
                   WHERE metric.project_id=? ORDER BY metric.created_at DESC""",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_table_cell(self, cell_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT cell.*, table_record.title table_title,
                          table_record.caption table_caption
                   FROM document_table_cells cell
                   JOIN document_tables table_record ON table_record.id=cell.table_id
                   WHERE cell.id=?""",
                (cell_id,),
            ).fetchone()
        return dict(row) if row else None

    def create_table_metric_aggregation(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO table_metric_aggregations
                   (id, display_key, project_id, document_id, claim_id, table_cell_id,
                    metric_name, aggregation_function, metric_ids_json, run_ids_json,
                    excluded_metric_ids_json, sample_count, computed_value,
                    reported_value, variance, tolerance, status, actor, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record["document_id"],
                    record.get("claim_id"),
                    record["table_cell_id"],
                    record["metric_name"],
                    record["aggregation_function"],
                    json.dumps(record["metric_ids"], ensure_ascii=False),
                    json.dumps(record["run_ids"], ensure_ascii=False),
                    json.dumps(record.get("excluded_metric_ids", []), ensure_ascii=False),
                    record["sample_count"],
                    record["computed_value"],
                    record["reported_value"],
                    record["variance"],
                    record["tolerance"],
                    record["status"],
                    record["actor"],
                    record.get("note", ""),
                    utc_now(),
                ),
            )
        return self.get_table_metric_aggregation(record["id"]) or {}

    def get_table_metric_aggregation(self, aggregation_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT aggregation.*, cell.value cell_value,
                          cell.source_locator cell_locator, table_record.title table_title,
                          document.title document_title, claim.display_key claim_key
                   FROM table_metric_aggregations aggregation
                   JOIN document_table_cells cell ON cell.id=aggregation.table_cell_id
                   JOIN document_tables table_record ON table_record.id=cell.table_id
                   JOIN scientific_documents document ON document.id=aggregation.document_id
                   LEFT JOIN claims claim ON claim.id=aggregation.claim_id
                   WHERE aggregation.id=?""",
                (aggregation_id,),
            ).fetchone()
        return _decode(row, "metric_ids", "run_ids", "excluded_metric_ids")

    def list_table_metric_aggregations(
        self,
        project_id: str,
        *,
        document_id: str | None = None,
        claim_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["aggregation.project_id=?"]
        values: list[Any] = [project_id]
        if document_id:
            clauses.append("aggregation.document_id=?")
            values.append(document_id)
        if claim_id:
            clauses.append("aggregation.claim_id=?")
            values.append(claim_id)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT aggregation.*, cell.value cell_value,
                           cell.source_locator cell_locator,
                           table_record.title table_title, claim.display_key claim_key
                    FROM table_metric_aggregations aggregation
                    JOIN document_table_cells cell ON cell.id=aggregation.table_cell_id
                    JOIN document_tables table_record ON table_record.id=cell.table_id
                    LEFT JOIN claims claim ON claim.id=aggregation.claim_id
                    WHERE {" AND ".join(clauses)}
                    ORDER BY aggregation.created_at DESC""",
                values,
            ).fetchall()
        return [_decode(row, "metric_ids", "run_ids", "excluded_metric_ids") or {} for row in rows]

    def remove_evidence(self, claim_id: str, evidence_id: str) -> dict[str, Any] | None:
        with self.database.transaction() as db:
            row = db.execute(
                "SELECT * FROM claim_evidence WHERE id=? AND claim_id=?",
                (evidence_id, claim_id),
            ).fetchone()
            if row:
                db.execute("DELETE FROM claim_evidence WHERE id=?", (evidence_id,))
        return dict(row) if row else None

    def record_evidence_event(self, record: dict[str, Any]) -> None:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO claim_evidence_events
                   (id, project_id, claim_id, claim_evidence_id, action, actor,
                    note, snapshot_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["project_id"],
                    record["claim_id"],
                    record["claim_evidence_id"],
                    record["action"],
                    record["actor"],
                    record.get("note", ""),
                    json.dumps(record.get("snapshot", {}), ensure_ascii=False),
                    utc_now(),
                ),
            )

    def list_evidence_events(self, claim_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT * FROM claim_evidence_events WHERE claim_id=?
                   ORDER BY created_at DESC""",
                (claim_id,),
            ).fetchall()
        return [_decode(row, "snapshot") or {} for row in rows]

    def list_evidence_options(
        self,
        project_id: str,
        evidence_type: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        needle = f"%{(query or '').strip()}%"
        options: list[dict[str, Any]] = []
        with self.database.connection() as db:
            if evidence_type in (None, "experiment_run"):
                rows = db.execute(
                    """SELECT r.id, 'experiment_run' evidence_type,
                              'ExperimentRun' entity_type,
                              r.display_key || ' · ' || r.name title,
                              e.title subtitle, r.id locator, r.commit_sha version
                       FROM experiment_runs r JOIN experiments e ON e.id=r.experiment_id
                       WHERE r.project_id=? AND (?='%%' OR r.name LIKE ? OR r.display_key LIKE ?)
                       ORDER BY r.created_at DESC LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
            if evidence_type in (None, "metric"):
                rows = db.execute(
                    """SELECT m.id, 'metric' evidence_type, 'Metric' entity_type,
                              m.name || ' = ' || CAST(m.value AS TEXT) title,
                              r.display_key || ' · ' || r.name subtitle,
                              m.id locator, r.commit_sha version
                       FROM run_metrics m JOIN experiment_runs r ON r.id=m.run_id
                       WHERE m.project_id=? AND (?='%%' OR m.name LIKE ? OR CAST(m.value AS TEXT) LIKE ?)
                       ORDER BY m.created_at DESC LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
            if evidence_type in (None, "artifact"):
                rows = db.execute(
                    """SELECT a.id, 'artifact' evidence_type, 'Artifact' entity_type,
                              a.name title, r.display_key || ' · ' || a.kind subtitle,
                              a.uri locator, r.commit_sha version
                       FROM run_artifacts a JOIN experiment_runs r ON r.id=a.run_id
                       WHERE a.project_id=? AND (?='%%' OR a.name LIKE ? OR a.uri LIKE ?)
                       ORDER BY a.created_at DESC LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
            if evidence_type in (None, "code"):
                rows = db.execute(
                    """SELECT entity.id, 'code' evidence_type,
                              entity.entity_type, entity.name title,
                              repo.name || ' · ' || entity.path subtitle,
                              entity.source_uri locator, entity.commit_sha version
                       FROM entities entity JOIN repositories repo
                         ON repo.id=entity.repository_id
                        AND repo.active_generation_id=entity.generation_id
                       WHERE entity.project_id=?
                         AND entity.entity_type IN ('CodeSymbol','FileVersion')
                         AND (?='%%' OR entity.name LIKE ? OR entity.path LIKE ?)
                       ORDER BY entity.name LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
            if evidence_type in (None, "codex"):
                rows = db.execute(
                    """SELECT item.id, 'codex' evidence_type,
                              item.item_type entity_type, item.name title,
                              thread.title subtitle, item.source_locator locator,
                              item.timestamp version
                       FROM codex_items item
                       JOIN codex_sources source ON source.id=item.source_id
                        AND source.active_generation_id=item.generation_id
                       JOIN codex_threads thread ON thread.thread_id=item.thread_id
                        AND thread.source_id=item.source_id
                        AND thread.generation_id=item.generation_id
                       WHERE source.project_id=?
                         AND (?='%%' OR item.name LIKE ? OR item.content LIKE ?)
                       ORDER BY item.timestamp DESC LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
            if evidence_type in (None, "document"):
                rows = db.execute(
                    """SELECT id, 'document' evidence_type,
                              'ScientificDocument' entity_type, title,
                              version subtitle, source_uri locator, version
                       FROM scientific_documents
                       WHERE project_id=? AND (?='%%' OR title LIKE ? OR content LIKE ?)
                       ORDER BY updated_at DESC LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
            if evidence_type in (None, "table_cell"):
                rows = db.execute(
                    """SELECT cell.id, 'table_cell' evidence_type,
                              'TableCell' entity_type,
                              table_record.title || ' · ' || cell.value title,
                              'row ' || CAST(cell.row_index AS TEXT) || ', col ' ||
                                CAST(cell.column_index AS TEXT) subtitle,
                              cell.source_locator locator, document.version version
                       FROM document_table_cells cell
                       JOIN document_tables table_record ON table_record.id=cell.table_id
                       JOIN scientific_documents document ON document.id=cell.document_id
                       WHERE cell.project_id=?
                         AND (?='%%' OR cell.value LIKE ? OR table_record.title LIKE ?)
                       ORDER BY cell.created_at DESC LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
            if evidence_type in (None, "figure"):
                rows = db.execute(
                    """SELECT figure.id, 'figure' evidence_type,
                              'Figure' entity_type,
                              'Figure ' || CAST(figure.ordinal AS TEXT) title,
                              figure.caption subtitle, figure.source_locator locator,
                              document.version version
                       FROM document_figures figure
                       JOIN scientific_documents document ON document.id=figure.document_id
                       WHERE figure.project_id=?
                         AND (?='%%' OR figure.caption LIKE ? OR figure.source_locator LIKE ?)
                       ORDER BY figure.created_at DESC LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
            if evidence_type in (None, "metric_aggregation"):
                rows = db.execute(
                    """SELECT aggregation.id, 'metric_aggregation' evidence_type,
                              'MetricAggregation' entity_type,
                              aggregation.display_key || ' · ' || aggregation.metric_name title,
                              aggregation.aggregation_function || '(' ||
                                CAST(aggregation.sample_count AS TEXT) || ') = ' ||
                                CAST(aggregation.computed_value AS TEXT) subtitle,
                              cell.source_locator locator, document.version version
                       FROM table_metric_aggregations aggregation
                       JOIN document_table_cells cell ON cell.id=aggregation.table_cell_id
                       JOIN scientific_documents document ON document.id=aggregation.document_id
                       WHERE aggregation.project_id=?
                         AND (?='%%' OR aggregation.metric_name LIKE ? OR
                              aggregation.display_key LIKE ?)
                       ORDER BY aggregation.created_at DESC LIMIT ?""",
                    (project_id, needle, needle, needle, limit),
                ).fetchall()
                options.extend(dict(item) for item in rows)
        return options[:limit]

    def list_documents(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.connection() as db:
            rows = db.execute(
                """SELECT d.*,
                          (SELECT count(*) FROM document_sections s
                           WHERE s.document_id=d.id) AS section_count,
                          (SELECT count(*) FROM claims c WHERE c.document_id=d.id) AS claim_count,
                          (SELECT count(*) FROM claims c
                           WHERE c.document_id=d.id AND c.status='verified') AS verified_claims
                   FROM scientific_documents d WHERE d.project_id=?
                   ORDER BY d.updated_at DESC""",
                (project_id,),
            ).fetchall()
        return [_decode(row, "authors", "tags") or {} for row in rows]

    def create_claim(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO claims
                   (id, display_key, project_id, document_id, section_id, content, claim_type,
                    status, extraction_method, extraction_confidence, source_locator,
                    metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'reported', 'human', 1.0, ?, ?, ?, ?)""",
                (
                    record["id"],
                    record["display_key"],
                    record["project_id"],
                    record["document_id"],
                    record.get("section_id"),
                    record["content"],
                    record["claim_type"],
                    record["source_locator"],
                    json.dumps(record.get("metadata", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return self.get_claim(record["id"]) or {}

    def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT c.*, d.title AS document_title, d.version AS document_version
                   FROM claims c JOIN scientific_documents d ON d.id=c.document_id
                   WHERE c.id=?""",
                (claim_id,),
            ).fetchone()
            if not row:
                return None
            evidence = db.execute(
                "SELECT * FROM claim_evidence WHERE claim_id=? ORDER BY created_at",
                (claim_id,),
            ).fetchall()
        result = _decode(row, "metadata") or {}
        result["evidence"] = [dict(item) for item in evidence]
        result["evidence_events"] = self.list_evidence_events(claim_id)
        return result

    def list_claims(
        self,
        project_id: str,
        *,
        document_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["c.project_id=?"]
        values: list[Any] = [project_id]
        if document_id:
            clauses.append("c.document_id=?")
            values.append(document_id)
        if status:
            clauses.append("c.status=?")
            values.append(status)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT c.*, d.title AS document_title, d.version AS document_version,
                           (SELECT count(*) FROM claim_evidence e
                            WHERE e.claim_id=c.id) AS evidence_count
                    FROM claims c JOIN scientific_documents d ON d.id=c.document_id
                    WHERE {" AND ".join(clauses)} ORDER BY c.updated_at DESC""",
                values,
            ).fetchall()
        return [_decode(row, "metadata") or {} for row in rows]

    def update_claim(self, claim_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        if changes:
            fields = []
            values = []
            for key, value in changes.items():
                is_json = key == "metadata"
                fields.append("metadata_json=?" if is_json else f"{key}=?")
                values.append(json.dumps(value, ensure_ascii=False) if is_json else value)
            fields.append("updated_at=?")
            values.extend([utc_now(), claim_id])
            with self.database.transaction() as db:
                db.execute(f"UPDATE claims SET {', '.join(fields)} WHERE id=?", values)
        return self.get_claim(claim_id)

    def add_evidence(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO claim_evidence
                   (id, project_id, claim_id, evidence_entity_id, evidence_type,
                    relationship, confidence, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(claim_id, evidence_entity_id, relationship) DO UPDATE SET
                     evidence_type=excluded.evidence_type,
                     confidence=excluded.confidence,
                     note=excluded.note""",
                (
                    record["id"],
                    record["project_id"],
                    record["claim_id"],
                    record["evidence_entity_id"],
                    record["evidence_type"],
                    record["relationship"],
                    record["confidence"],
                    record["note"],
                    utc_now(),
                ),
            )
            row = db.execute(
                """SELECT * FROM claim_evidence
                   WHERE claim_id=? AND evidence_entity_id=? AND relationship=?""",
                (record["claim_id"], record["evidence_entity_id"], record["relationship"]),
            ).fetchone()
        return dict(row) if row else {}

    def set_validation(self, claim_id: str, status: str, validation: dict[str, Any]) -> None:
        claim = self.get_claim(claim_id)
        metadata = (claim or {}).get("metadata", {})
        metadata["validation"] = validation
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """UPDATE claims SET status=?, metadata_json=?, last_validated_at=?,
                   updated_at=? WHERE id=?""",
                (status, json.dumps(metadata, ensure_ascii=False), now, now, claim_id),
            )

    def stats(self, project_id: str) -> dict[str, int]:
        with self.database.connection() as db:
            row = db.execute(
                """SELECT
                     (SELECT count(*) FROM scientific_documents WHERE project_id=?) documents,
                     (SELECT count(*) FROM claims WHERE project_id=?) claims,
                     (SELECT count(*) FROM claims WHERE project_id=? AND status='verified') verified,
                     (SELECT count(*) FROM claims WHERE project_id=?
                      AND status IN ('contradicted','potentially_stale','insufficient_evidence')) at_risk,
                     (SELECT count(*) FROM claim_evidence WHERE project_id=?) evidence_links""",
                (project_id, project_id, project_id, project_id, project_id),
            ).fetchone()
        return {key: int(value or 0) for key, value in dict(row).items()}
