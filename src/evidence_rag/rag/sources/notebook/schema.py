"""Isolated additive Notebook Source V2 schema.

This schema is intentionally not imported by the application-wide database initializer.
The owner is :class:`NotebookV2Store`, which accepts only an explicit isolated path.
"""

from __future__ import annotations

NOTEBOOK_V2_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS notebook_templates_v2 (
    template_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    name TEXT NOT NULL,
    language TEXT,
    kernel_name TEXT,
    locator TEXT NOT NULL,
    active_revision_id TEXT,
    active_execution_id TEXT,
    UNIQUE(project_id, source_key)
);

CREATE TABLE IF NOT EXISTS notebook_revisions (
    revision_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    metadata_sha256 TEXT NOT NULL,
    nbformat INTEGER NOT NULL,
    nbformat_minor INTEGER NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(template_id) REFERENCES notebook_templates_v2(template_id),
    UNIQUE(template_id, generation_id, source_version, content_sha256)
);

CREATE TABLE IF NOT EXISTS notebook_executions (
    execution_id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    execution_key TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    content_sha256 TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(template_id) REFERENCES notebook_templates_v2(template_id),
    FOREIGN KEY(revision_id) REFERENCES notebook_revisions(revision_id),
    UNIQUE(template_id, generation_id, execution_key)
);

CREATE TABLE IF NOT EXISTS notebook_cell_versions (
    cell_version_id TEXT PRIMARY KEY,
    stable_cell_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    cell_type TEXT NOT NULL,
    source TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    native_cell_id TEXT,
    identity_confidence TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(revision_id) REFERENCES notebook_revisions(revision_id),
    UNIQUE(revision_id, display_order),
    UNIQUE(revision_id, stable_cell_id)
);

CREATE TABLE IF NOT EXISTS notebook_cell_executions (
    cell_execution_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    cell_version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    execution_count INTEGER,
    execution_order INTEGER,
    state TEXT NOT NULL,
    stale INTEGER NOT NULL CHECK(stale IN (0, 1)),
    diagnostics_json TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(execution_id) REFERENCES notebook_executions(execution_id),
    FOREIGN KEY(cell_version_id) REFERENCES notebook_cell_versions(cell_version_id),
    UNIQUE(execution_id, display_order),
    UNIQUE(execution_id, cell_version_id)
);

CREATE TABLE IF NOT EXISTS notebook_parameters (
    parameter_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    cell_version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value_type TEXT NOT NULL,
    canonical_value TEXT NOT NULL,
    value_sha256 TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(execution_id) REFERENCES notebook_executions(execution_id),
    FOREIGN KEY(cell_version_id) REFERENCES notebook_cell_versions(cell_version_id),
    UNIQUE(execution_id, name)
);

CREATE TABLE IF NOT EXISTS notebook_symbols (
    symbol_id TEXT PRIMARY KEY,
    cell_version_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    confidence TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(cell_version_id) REFERENCES notebook_cell_versions(cell_version_id),
    FOREIGN KEY(revision_id) REFERENCES notebook_revisions(revision_id),
    UNIQUE(cell_version_id, name, role)
);

CREATE TABLE IF NOT EXISTS notebook_artifacts (
    artifact_id TEXT PRIMARY KEY,
    execution_id TEXT NOT NULL,
    cell_execution_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    artifact_type TEXT NOT NULL,
    mime_types_json TEXT NOT NULL,
    text_content TEXT NOT NULL,
    error_name TEXT,
    error_value TEXT,
    binary_omitted INTEGER NOT NULL CHECK(binary_omitted IN (0, 1)),
    metric_confirmed INTEGER NOT NULL CHECK(metric_confirmed = 0),
    content_sha256 TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(execution_id) REFERENCES notebook_executions(execution_id),
    FOREIGN KEY(cell_execution_id) REFERENCES notebook_cell_executions(cell_execution_id),
    UNIQUE(cell_execution_id, ordinal)
);

CREATE TABLE IF NOT EXISTS notebook_retrieval_units (
    unit_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL,
    execution_id TEXT,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    unit_type TEXT NOT NULL,
    profile TEXT NOT NULL,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(revision_id) REFERENCES notebook_revisions(revision_id),
    FOREIGN KEY(execution_id) REFERENCES notebook_executions(execution_id)
);

CREATE TABLE IF NOT EXISTS notebook_edges (
    edge_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL,
    execution_id TEXT,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    confidence TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(revision_id) REFERENCES notebook_revisions(revision_id),
    FOREIGN KEY(execution_id) REFERENCES notebook_executions(execution_id)
);

CREATE TABLE IF NOT EXISTS notebook_comparisons_v2 (
    comparison_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    baseline_revision_id TEXT NOT NULL,
    candidate_revision_id TEXT NOT NULL,
    matches_sha256 TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(baseline_revision_id) REFERENCES notebook_revisions(revision_id),
    FOREIGN KEY(candidate_revision_id) REFERENCES notebook_revisions(revision_id)
);

CREATE TABLE IF NOT EXISTS notebook_publications_v2 (
    publication_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_payload_sha256 TEXT NOT NULL,
    template_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    publication_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(template_id) REFERENCES notebook_templates_v2(template_id),
    FOREIGN KEY(revision_id) REFERENCES notebook_revisions(revision_id),
    FOREIGN KEY(execution_id) REFERENCES notebook_executions(execution_id)
);

CREATE TABLE IF NOT EXISTS notebook_tombstones_v2 (
    tombstone_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    reason TEXT NOT NULL,
    tombstone_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(template_id) REFERENCES notebook_templates_v2(template_id),
    UNIQUE(project_id, generation_id, template_id)
);

CREATE INDEX IF NOT EXISTS idx_notebook_revisions_scope
    ON notebook_revisions(project_id, acl_ref, generation_id, template_id);
CREATE INDEX IF NOT EXISTS idx_notebook_executions_scope
    ON notebook_executions(project_id, acl_ref, generation_id, template_id);
CREATE INDEX IF NOT EXISTS idx_notebook_cells_revision
    ON notebook_cell_versions(revision_id, display_order);
CREATE INDEX IF NOT EXISTS idx_notebook_cell_exec_execution
    ON notebook_cell_executions(execution_id, display_order);
CREATE INDEX IF NOT EXISTS idx_notebook_artifacts_execution
    ON notebook_artifacts(execution_id, cell_execution_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_notebook_tombstones_scope
    ON notebook_tombstones_v2(project_id, acl_ref, generation_id, template_id);
"""

NOTEBOOK_V2_TABLES = (
    "notebook_templates_v2",
    "notebook_revisions",
    "notebook_executions",
    "notebook_cell_versions",
    "notebook_cell_executions",
    "notebook_parameters",
    "notebook_symbols",
    "notebook_artifacts",
    "notebook_retrieval_units",
    "notebook_edges",
    "notebook_comparisons_v2",
    "notebook_publications_v2",
    "notebook_tombstones_v2",
)

__all__ = ["NOTEBOOK_V2_SCHEMA", "NOTEBOOK_V2_TABLES"]
