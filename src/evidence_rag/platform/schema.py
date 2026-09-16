from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS drift_assessments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    original_commit TEXT NOT NULL,
    current_commit TEXT NOT NULL,
    status TEXT NOT NULL,
    risk_score REAL NOT NULL,
    reason TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    assessed_at TEXT NOT NULL,
    UNIQUE(claim_id, run_id, repository_id)
);
CREATE INDEX IF NOT EXISTS idx_drift_project
    ON drift_assessments(project_id, status, risk_score DESC, assessed_at DESC);
"""
