from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    iteration_id TEXT,
    title TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    hypothesis TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(iteration_id) REFERENCES research_iterations(id)
);
CREATE INDEX IF NOT EXISTS idx_experiments_project
    ON experiments(project_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS experiment_runs (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    external_id TEXT,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    repository_id TEXT,
    commit_sha TEXT,
    commit_entity_id TEXT,
    branch TEXT,
    dataset_id TEXT,
    dataset_version TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    environment_json TEXT NOT NULL DEFAULT '{}',
    command TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    completed_at TEXT,
    tags_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(experiment_id) REFERENCES experiments(id),
    UNIQUE(experiment_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_experiment
    ON experiment_runs(project_id, experiment_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS run_metrics (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    split TEXT,
    step INTEGER,
    higher_is_better INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES experiment_runs(id),
    UNIQUE(run_id, name, split, step)
);
CREATE INDEX IF NOT EXISTS idx_metrics_run ON run_metrics(run_id, name);

CREATE TABLE IF NOT EXISTS run_artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    uri TEXT NOT NULL,
    kind TEXT NOT NULL,
    checksum TEXT,
    media_type TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES experiment_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON run_artifacts(run_id, kind);

CREATE TABLE IF NOT EXISTS experiment_comparisons (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    name TEXT NOT NULL,
    baseline_run_id TEXT NOT NULL,
    candidate_run_ids_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(experiment_id) REFERENCES experiments(id)
);
CREATE INDEX IF NOT EXISTS idx_comparisons_experiment
    ON experiment_comparisons(experiment_id, created_at DESC);

CREATE TABLE IF NOT EXISTS experiment_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    adapter_type TEXT NOT NULL,
    tracking_uri TEXT NOT NULL,
    status TEXT NOT NULL,
    stats_json TEXT NOT NULL DEFAULT '{}',
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, adapter_type, tracking_uri)
);

CREATE TABLE IF NOT EXISTS external_experiment_mappings (
    source_id TEXT NOT NULL,
    external_experiment_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source_id, external_experiment_id),
    FOREIGN KEY(source_id) REFERENCES experiment_sources(id),
    FOREIGN KEY(experiment_id) REFERENCES experiments(id)
);
"""
