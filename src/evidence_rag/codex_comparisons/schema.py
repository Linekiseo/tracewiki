from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS codex_comparisons (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    baseline_thread_id TEXT NOT NULL,
    thread_ids_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_codex_comparisons_project
    ON codex_comparisons(project_id, created_at DESC);
"""
