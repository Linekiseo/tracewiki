from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS evaluation_cases (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    question TEXT NOT NULL,
    expected_sources_json TEXT NOT NULL DEFAULT '[]',
    expected_entity_ids_json TEXT NOT NULL DEFAULT '[]',
    expected_paths_json TEXT NOT NULL DEFAULT '[]',
    expected_commit_ids_json TEXT NOT NULL DEFAULT '[]',
    forbidden_entity_ids_json TEXT NOT NULL DEFAULT '[]',
    required_version TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evaluation_cases
    ON evaluation_cases(project_id, enabled, updated_at DESC);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    case_count INTEGER NOT NULL,
    source_domain TEXT NOT NULL DEFAULT 'global',
    runner_version TEXT NOT NULL DEFAULT 'evaluation-v1',
    dataset_id TEXT,
    dataset_version TEXT,
    dataset_package_hash TEXT,
    graph_candidate_enabled INTEGER,
    paired_graph_off_run_id TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    config_json TEXT NOT NULL DEFAULT '{}',
    failure_json TEXT NOT NULL DEFAULT '{}',
    summary_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS evaluation_results (
    id TEXT PRIMARY KEY,
    evaluation_run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    passed INTEGER NOT NULL,
    source_recall REAL NOT NULL,
    entity_recall REAL NOT NULL,
    citation_completeness REAL NOT NULL,
    version_accuracy REAL NOT NULL,
    evidence_path_recall REAL NOT NULL DEFAULT 1.0,
    commit_accuracy REAL NOT NULL DEFAULT 1.0,
    wrong_version_rate REAL NOT NULL DEFAULT 0.0,
    unauthorized_leakage REAL NOT NULL DEFAULT 0.0,
    latency_ms REAL NOT NULL,
    result_entity_ids_json TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(evaluation_run_id) REFERENCES evaluation_runs(id),
    FOREIGN KEY(case_id) REFERENCES evaluation_cases(id)
);
CREATE INDEX IF NOT EXISTS idx_evaluation_results
    ON evaluation_results(evaluation_run_id, passed, created_at);

CREATE TABLE IF NOT EXISTS evaluation_case_profiles (
    case_id TEXT PRIMARY KEY,
    source_domain TEXT NOT NULL,
    task TEXT NOT NULL,
    dataset_id TEXT,
    dataset_version TEXT,
    dataset_package_hash TEXT,
    query_profile_json TEXT NOT NULL DEFAULT '{}',
    expected_unit_ids_json TEXT NOT NULL DEFAULT '[]',
    expected_entity_types_json TEXT NOT NULL DEFAULT '[]',
    expected_locators_json TEXT NOT NULL DEFAULT '[]',
    required_paths_json TEXT NOT NULL DEFAULT '[]',
    required_edge_types_json TEXT NOT NULL DEFAULT '[]',
    acceptable_alternative_groups_json TEXT NOT NULL DEFAULT '[]',
    expected_context_roles_json TEXT NOT NULL DEFAULT '[]',
    expected_ref TEXT,
    expected_answer_mode TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(case_id) REFERENCES evaluation_cases(id)
);
CREATE INDEX IF NOT EXISTS idx_evaluation_case_profiles
    ON evaluation_case_profiles(source_domain, task, updated_at DESC);

CREATE TABLE IF NOT EXISTS evaluation_candidate_judgments (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    entity_id TEXT,
    retrieval_unit_id TEXT,
    relevance_grade INTEGER NOT NULL CHECK(relevance_grade IN (-1, 0, 1, 2)),
    necessity_role TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(entity_id IS NOT NULL OR retrieval_unit_id IS NOT NULL),
    FOREIGN KEY(case_id) REFERENCES evaluation_cases(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evaluation_candidate_judgments_target
    ON evaluation_candidate_judgments(
        case_id,
        COALESCE(entity_id, ''),
        COALESCE(retrieval_unit_id, '')
    );
CREATE INDEX IF NOT EXISTS idx_evaluation_candidate_judgments_case
    ON evaluation_candidate_judgments(case_id, relevance_grade);

CREATE TABLE IF NOT EXISTS evaluation_metric_values (
    id TEXT PRIMARY KEY,
    evaluation_run_id TEXT NOT NULL,
    case_id TEXT,
    source_domain TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    slice_json TEXT NOT NULL DEFAULT '{}',
    value REAL,
    status TEXT NOT NULL DEFAULT 'available'
        CHECK(status IN ('available', 'unavailable')),
    unavailable_reason TEXT,
    numerator REAL,
    denominator REAL,
    eligible INTEGER NOT NULL DEFAULT 1,
    total_cases INTEGER,
    eligible_cases INTEGER,
    available_cases INTEGER,
    unavailable_cases INTEGER,
    created_at TEXT NOT NULL,
    CHECK(
        (status = 'available' AND value IS NOT NULL)
        OR (status = 'unavailable' AND unavailable_reason IS NOT NULL)
    ),
    FOREIGN KEY(evaluation_run_id) REFERENCES evaluation_runs(id),
    FOREIGN KEY(case_id) REFERENCES evaluation_cases(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evaluation_metric_values_key
    ON evaluation_metric_values(
        evaluation_run_id,
        COALESCE(case_id, ''),
        source_domain,
        metric_name,
        slice_json
    );
CREATE INDEX IF NOT EXISTS idx_evaluation_metric_values_run
    ON evaluation_metric_values(evaluation_run_id, case_id, metric_name);

CREATE TRIGGER IF NOT EXISTS trg_evaluation_runs_terminal_update
BEFORE UPDATE ON evaluation_runs
WHEN OLD.status IN ('completed', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'terminal evaluation run is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_runs_terminal_delete
BEFORE DELETE ON evaluation_runs
WHEN OLD.status IN ('completed', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'terminal evaluation run is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_results_running_insert
BEFORE INSERT ON evaluation_results
WHEN COALESCE(
    (SELECT status FROM evaluation_runs WHERE id=NEW.evaluation_run_id),
    ''
) <> 'running'
BEGIN
    SELECT RAISE(ABORT, 'evaluation results require a running run');
END;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_results_terminal_update
BEFORE UPDATE ON evaluation_results
WHEN (SELECT status FROM evaluation_runs WHERE id=OLD.evaluation_run_id)
     IN ('completed', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'terminal evaluation result is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_results_terminal_delete
BEFORE DELETE ON evaluation_results
WHEN (SELECT status FROM evaluation_runs WHERE id=OLD.evaluation_run_id)
     IN ('completed', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'terminal evaluation result is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_metrics_running_insert
BEFORE INSERT ON evaluation_metric_values
WHEN COALESCE(
    (SELECT status FROM evaluation_runs WHERE id=NEW.evaluation_run_id),
    ''
) <> 'running'
BEGIN
    SELECT RAISE(ABORT, 'evaluation metrics require a running run');
END;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_metrics_terminal_update
BEFORE UPDATE ON evaluation_metric_values
WHEN (SELECT status FROM evaluation_runs WHERE id=OLD.evaluation_run_id)
     IN ('completed', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'terminal evaluation metric is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_evaluation_metrics_terminal_delete
BEFORE DELETE ON evaluation_metric_values
WHEN (SELECT status FROM evaluation_runs WHERE id=OLD.evaluation_run_id)
     IN ('completed', 'failed')
BEGIN
    SELECT RAISE(ABORT, 'terminal evaluation metric is immutable');
END;

CREATE TABLE IF NOT EXISTS query_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    query_text TEXT NOT NULL,
    source_count INTEGER NOT NULL,
    result_count INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_query_events
    ON query_events(project_id, created_at DESC);
"""
