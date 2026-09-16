from __future__ import annotations

import json
from typing import Any

from ..storage import SQLiteStore, utc_now
from .schema import SCHEMA


class NotebookStore:
    def __init__(self, database: SQLiteStore) -> None:
        self.database = database

    def initialize(self) -> None:
        with self.database.connection() as db:
            db.executescript(SCHEMA)

    def save(
        self, template: dict[str, Any], run: dict[str, Any], cells: list[dict[str, Any]]
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction() as db:
            db.execute(
                """INSERT INTO notebook_templates
                   (id, project_id, name, source_uri, content_hash, kernel_name,
                    language, acl_ref, metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(project_id, source_uri) DO UPDATE SET
                     name=excluded.name, content_hash=excluded.content_hash,
                     kernel_name=excluded.kernel_name, language=excluded.language,
                     acl_ref=CASE
                       WHEN notebook_templates.acl_ref='public'
                         AND excluded.acl_ref<>'public' THEN excluded.acl_ref
                       ELSE notebook_templates.acl_ref
                     END,
                     metadata_json=excluded.metadata_json,
                     updated_at=excluded.updated_at""",
                (
                    template["id"],
                    template["project_id"],
                    template["name"],
                    template["source_uri"],
                    template["content_hash"],
                    template.get("kernel_name"),
                    template.get("language"),
                    template["acl_ref"],
                    json.dumps(template.get("metadata", {}), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            template_row = db.execute(
                "SELECT id FROM notebook_templates WHERE project_id=? AND source_uri=?",
                (template["project_id"], template["source_uri"]),
            ).fetchone()
            template_id = template_row["id"]
            inserted = db.execute(
                """INSERT INTO notebook_runs
                   (id, project_id, template_id, experiment_id, run_id, version, status,
                    started_at, completed_at, parameters_json, raw_object_id,
                    metadata_json, created_at, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(template_id, version, content_hash) DO NOTHING""",
                (
                    run["id"],
                    run["project_id"],
                    template_id,
                    run.get("experiment_id"),
                    run.get("run_id"),
                    run["version"],
                    run["status"],
                    run.get("started_at"),
                    run.get("completed_at"),
                    json.dumps(run.get("parameters", {}), ensure_ascii=False),
                    run.get("raw_object_id"),
                    json.dumps(run.get("metadata", {}), ensure_ascii=False),
                    now,
                    run["content_hash"],
                ),
            )
            row = db.execute(
                """SELECT id FROM notebook_runs
                   WHERE template_id=? AND version=? AND content_hash=?""",
                (template_id, run["version"], run["content_hash"]),
            ).fetchone()
            run_id = row["id"]
            if inserted.rowcount == 1:
                for cell in cells:
                    db.execute(
                        """INSERT INTO notebook_cells
                           (id, project_id, notebook_run_id, ordinal, cell_type, source,
                            source_hash, execution_count, tags_json, metadata_json,
                            source_locator, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            cell["id"],
                            run["project_id"],
                            run_id,
                            cell["ordinal"],
                            cell["cell_type"],
                            cell["source"],
                            cell["source_hash"],
                            cell.get("execution_count"),
                            json.dumps(cell.get("tags", [])),
                            json.dumps(cell.get("metadata", {}), ensure_ascii=False),
                            cell["source_locator"],
                            now,
                        ),
                    )
                    db.executemany(
                        """INSERT INTO notebook_outputs
                           (id, project_id, cell_id, ordinal, output_type, text_content,
                            data_types_json, error_name, error_value, artifact_ref,
                            metadata_json, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        [
                            (
                                output["id"],
                                run["project_id"],
                                cell["id"],
                                output["ordinal"],
                                output["output_type"],
                                output["text_content"],
                                json.dumps(output.get("data_types", [])),
                                output.get("error_name"),
                                output.get("error_value"),
                                output.get("artifact_ref"),
                                json.dumps(output.get("metadata", {}), ensure_ascii=False),
                                now,
                            )
                            for output in cell.get("outputs", [])
                        ],
                    )
        return self.get_run(run_id) or {}

    def get_run(
        self,
        notebook_run_id: str,
        *,
        allowed_acl_refs: list[str] | tuple[str, ...] | None = None,
        enforce_acl: bool = False,
    ) -> dict[str, Any] | None:
        allowed = tuple(sorted({"public", *(allowed_acl_refs or ())}))
        acl_clause = ""
        values: list[Any] = [notebook_run_id]
        if enforce_acl:
            marks = ",".join("?" for _ in allowed)
            acl_clause = f" AND t.acl_ref IN ({marks})"
            values.extend(allowed)
        with self.database.connection() as db:
            row = db.execute(
                f"""SELECT n.*, t.name template_name, t.source_uri, t.kernel_name, t.language,
                           t.acl_ref
                    FROM notebook_runs n JOIN notebook_templates t ON t.id=n.template_id
                    WHERE n.id=?{acl_clause}""",
                values,
            ).fetchone()
            if not row:
                return None
            cells = db.execute(
                "SELECT * FROM notebook_cells WHERE notebook_run_id=? ORDER BY ordinal",
                (notebook_run_id,),
            ).fetchall()
            outputs = db.execute(
                """SELECT o.* FROM notebook_outputs o JOIN notebook_cells c ON c.id=o.cell_id
                   WHERE c.notebook_run_id=? ORDER BY c.ordinal, o.ordinal""",
                (notebook_run_id,),
            ).fetchall()
        result = dict(row)
        for field in ("parameters_json", "metadata_json"):
            result[field.removesuffix("_json")] = json.loads(result.pop(field) or "{}")
        by_cell: dict[str, list[dict[str, Any]]] = {}
        for raw in outputs:
            item = dict(raw)
            item["data_types"] = json.loads(item.pop("data_types_json") or "[]")
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            by_cell.setdefault(item["cell_id"], []).append(item)
        result["cells"] = []
        for raw in cells:
            item = dict(raw)
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            item["outputs"] = by_cell.get(item["id"], [])
            result["cells"].append(item)
        return result

    def list_runs(
        self,
        project_id: str,
        *,
        allowed_acl_refs: list[str] | tuple[str, ...] | None = None,
        enforce_acl: bool = False,
    ) -> list[dict[str, Any]]:
        allowed = tuple(sorted({"public", *(allowed_acl_refs or ())}))
        acl_clause = ""
        values: list[Any] = [project_id]
        if enforce_acl:
            marks = ",".join("?" for _ in allowed)
            acl_clause = f" AND t.acl_ref IN ({marks})"
            values.extend(allowed)
        with self.database.connection() as db:
            rows = db.execute(
                f"""SELECT n.*, t.name template_name, t.source_uri, t.acl_ref,
                           (SELECT count(*) FROM notebook_cells c
                            WHERE c.notebook_run_id=n.id) cell_count,
                           (SELECT count(*) FROM notebook_outputs o
                            JOIN notebook_cells c ON c.id=o.cell_id
                            WHERE c.notebook_run_id=n.id) output_count
                    FROM notebook_runs n JOIN notebook_templates t ON t.id=n.template_id
                    WHERE n.project_id=?{acl_clause} ORDER BY n.created_at DESC""",
                values,
            ).fetchall()
        results = []
        for raw in rows:
            item = dict(raw)
            item["parameters"] = json.loads(item.pop("parameters_json") or "{}")
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            results.append(item)
        return results
