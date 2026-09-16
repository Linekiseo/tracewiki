from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS notebook_templates (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    kernel_name TEXT,
    language TEXT,
    acl_ref TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, source_uri)
);

CREATE TABLE IF NOT EXISTS notebook_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    experiment_id TEXT,
    run_id TEXT,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    parameters_json TEXT NOT NULL DEFAULT '{}',
    raw_object_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY(template_id) REFERENCES notebook_templates(id),
    UNIQUE(template_id, version, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_notebook_runs_project
    ON notebook_runs(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS notebook_cells (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    notebook_run_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    cell_type TEXT NOT NULL,
    source TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    execution_count INTEGER,
    tags_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    source_locator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(notebook_run_id, ordinal),
    FOREIGN KEY(notebook_run_id) REFERENCES notebook_runs(id)
);

CREATE TABLE IF NOT EXISTS notebook_outputs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    cell_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    output_type TEXT NOT NULL,
    text_content TEXT NOT NULL DEFAULT '',
    data_types_json TEXT NOT NULL DEFAULT '[]',
    error_name TEXT,
    error_value TEXT,
    artifact_ref TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(cell_id) REFERENCES notebook_cells(id)
);
"""
