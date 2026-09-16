from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS scientific_documents (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    iteration_id TEXT,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    content TEXT NOT NULL,
    authors_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'indexed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(iteration_id) REFERENCES research_iterations(id),
    UNIQUE(project_id, title, version)
);
CREATE INDEX IF NOT EXISTS idx_documents_project
    ON scientific_documents(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS document_sections (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    level INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    source_locator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES scientific_documents(id)
);
CREATE INDEX IF NOT EXISTS idx_sections_document
    ON document_sections(document_id, ordinal);

CREATE TABLE IF NOT EXISTS document_pages (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    content TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(document_id, page_number),
    FOREIGN KEY(document_id) REFERENCES scientific_documents(id)
);

CREATE TABLE IF NOT EXISTS document_tables (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    section_id TEXT,
    page_number INTEGER,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    caption TEXT NOT NULL DEFAULT '',
    row_count INTEGER NOT NULL,
    column_count INTEGER NOT NULL,
    extraction_confidence REAL NOT NULL,
    source_locator TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES scientific_documents(id)
);
CREATE INDEX IF NOT EXISTS idx_document_tables ON document_tables(document_id, ordinal);

CREATE TABLE IF NOT EXISTS document_table_cells (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    column_index INTEGER NOT NULL,
    value TEXT NOT NULL,
    is_header INTEGER NOT NULL DEFAULT 0,
    source_locator TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(table_id, row_index, column_index),
    FOREIGN KEY(table_id) REFERENCES document_tables(id)
);

CREATE TABLE IF NOT EXISTS document_figures (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    page_number INTEGER,
    ordinal INTEGER NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    source_locator TEXT NOT NULL,
    artifact_ref TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES scientific_documents(id)
);

CREATE TABLE IF NOT EXISTS document_citations (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    marker TEXT NOT NULL,
    context TEXT NOT NULL,
    target TEXT,
    source_locator TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES scientific_documents(id)
);

CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    section_id TEXT,
    content TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'reported',
    extraction_method TEXT NOT NULL,
    extraction_confidence REAL NOT NULL,
    source_locator TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    last_validated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES scientific_documents(id),
    FOREIGN KEY(section_id) REFERENCES document_sections(id)
);
CREATE INDEX IF NOT EXISTS idx_claims_project
    ON claims(project_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_claims_document ON claims(document_id, section_id);

CREATE TABLE IF NOT EXISTS claim_evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    evidence_entity_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    relationship TEXT NOT NULL,
    confidence REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(claim_id, evidence_entity_id, relationship),
    FOREIGN KEY(claim_id) REFERENCES claims(id)
);
CREATE INDEX IF NOT EXISTS idx_claim_evidence
    ON claim_evidence(claim_id, relationship);

CREATE TABLE IF NOT EXISTS claim_match_candidates (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    score REAL NOT NULL,
    signals_json TEXT NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(claim_id, run_id),
    FOREIGN KEY(claim_id) REFERENCES claims(id),
    FOREIGN KEY(run_id) REFERENCES experiment_runs(id)
);

CREATE TABLE IF NOT EXISTS claim_match_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    relationship TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    claim_evidence_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES claim_match_candidates(id)
);
CREATE INDEX IF NOT EXISTS idx_claim_match_reviews
    ON claim_match_reviews(candidate_id, created_at DESC);

CREATE TABLE IF NOT EXISTS table_metric_match_candidates (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    claim_id TEXT,
    table_id TEXT NOT NULL,
    table_cell_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    score REAL NOT NULL,
    signals_json TEXT NOT NULL DEFAULT '{}',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES scientific_documents(id),
    FOREIGN KEY(claim_id) REFERENCES claims(id),
    FOREIGN KEY(table_id) REFERENCES document_tables(id),
    FOREIGN KEY(table_cell_id) REFERENCES document_table_cells(id),
    FOREIGN KEY(metric_id) REFERENCES run_metrics(id),
    FOREIGN KEY(run_id) REFERENCES experiment_runs(id)
);
CREATE INDEX IF NOT EXISTS idx_table_metric_candidates
    ON table_metric_match_candidates(project_id, document_id, review_status, score DESC);
CREATE INDEX IF NOT EXISTS idx_table_metric_claim_candidates
    ON table_metric_match_candidates(claim_id, review_status, score DESC);

CREATE TABLE IF NOT EXISTS table_metric_match_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    claim_evidence_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES table_metric_match_candidates(id)
);
CREATE INDEX IF NOT EXISTS idx_table_metric_match_reviews
    ON table_metric_match_reviews(candidate_id, created_at DESC);

CREATE TABLE IF NOT EXISTS table_metric_aggregations (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    claim_id TEXT,
    table_cell_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    aggregation_function TEXT NOT NULL,
    metric_ids_json TEXT NOT NULL,
    run_ids_json TEXT NOT NULL,
    excluded_metric_ids_json TEXT NOT NULL DEFAULT '[]',
    sample_count INTEGER NOT NULL,
    computed_value REAL NOT NULL,
    reported_value REAL NOT NULL,
    variance REAL NOT NULL,
    tolerance REAL NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES scientific_documents(id),
    FOREIGN KEY(claim_id) REFERENCES claims(id),
    FOREIGN KEY(table_cell_id) REFERENCES document_table_cells(id)
);
CREATE INDEX IF NOT EXISTS idx_table_metric_aggregations
    ON table_metric_aggregations(project_id, document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_table_metric_aggregations_claim
    ON table_metric_aggregations(claim_id, created_at DESC);

CREATE TABLE IF NOT EXISTS claim_evidence_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    claim_evidence_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(claim_id) REFERENCES claims(id)
);
CREATE INDEX IF NOT EXISTS idx_claim_evidence_events
    ON claim_evidence_events(claim_id, created_at DESC);
"""
