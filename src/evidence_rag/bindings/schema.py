from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS binding_scans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    counts_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS binding_candidates (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    binding_type TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    source_thread_id TEXT NOT NULL,
    source_turn_id TEXT,
    source_item_type TEXT NOT NULL,
    source_title TEXT NOT NULL,
    changed_path TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    repository_name TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    target_generation_id TEXT NOT NULL,
    target_path TEXT NOT NULL,
    target_commit_id TEXT,
    target_commit_sha TEXT,
    target_symbol_ids_json TEXT NOT NULL DEFAULT '[]',
    derivation TEXT NOT NULL,
    confidence REAL NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    signals_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_entity_id, repository_id, changed_path, target_entity_id),
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(scan_id) REFERENCES binding_scans(id)
);
CREATE INDEX IF NOT EXISTS idx_binding_review_queue
    ON binding_candidates(project_id, review_status, confidence DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_binding_source
    ON binding_candidates(project_id, source_thread_id, source_entity_id);

CREATE TABLE IF NOT EXISTS unmatched_changes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    scan_id TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    source_thread_id TEXT NOT NULL,
    changed_path TEXT NOT NULL,
    repository_id TEXT,
    reason TEXT NOT NULL,
    signals_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(source_entity_id, changed_path, repository_id),
    FOREIGN KEY(scan_id) REFERENCES binding_scans(id)
);
"""
