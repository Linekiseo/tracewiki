from __future__ import annotations

WORKSPACE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'internal',
    status TEXT NOT NULL DEFAULT 'active',
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_topics (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    problem_statement TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    owner TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_topics_project
    ON research_topics(project_id, status, priority, updated_at);

CREATE TABLE IF NOT EXISTS research_iterations (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    hypothesis TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    starts_at TEXT,
    target_at TEXT,
    completed_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(topic_id) REFERENCES research_topics(id)
);
CREATE INDEX IF NOT EXISTS idx_iterations_topic
    ON research_iterations(project_id, topic_id, status, updated_at);

CREATE TABLE IF NOT EXISTS research_work_items (
    id TEXT PRIMARY KEY,
    display_key TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    topic_id TEXT,
    iteration_id TEXT,
    repository_id TEXT,
    title TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    assignee_type TEXT NOT NULL DEFAULT 'human',
    assignee TEXT NOT NULL,
    workspace_path TEXT,
    base_ref TEXT,
    due_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(topic_id) REFERENCES research_topics(id),
    FOREIGN KEY(iteration_id) REFERENCES research_iterations(id),
    FOREIGN KEY(repository_id) REFERENCES repositories(id)
);
CREATE INDEX IF NOT EXISTS idx_work_items_project
    ON research_work_items(project_id, status, priority, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_work_items_topic
    ON research_work_items(project_id, topic_id, iteration_id, status);

CREATE TABLE IF NOT EXISTS iteration_links (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    iteration_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    source_type TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(iteration_id, entity_id, role),
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(iteration_id) REFERENCES research_iterations(id)
);
CREATE INDEX IF NOT EXISTS idx_iteration_links_entity
    ON iteration_links(project_id, entity_id, source_type);

CREATE TABLE IF NOT EXISTS platform_edges (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    evidence_entity_id TEXT,
    derivation TEXT NOT NULL,
    confidence REAL NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    valid_from TEXT,
    valid_to TEXT,
    rule_version TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, source_entity_id, predicate, target_entity_id, evidence_entity_id),
    FOREIGN KEY(project_id) REFERENCES projects(id)
);
CREATE INDEX IF NOT EXISTS idx_platform_edges_source
    ON platform_edges(project_id, source_entity_id, predicate, review_status);
CREATE INDEX IF NOT EXISTS idx_platform_edges_target
    ON platform_edges(project_id, target_entity_id, predicate, review_status);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    trace_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_project_time
    ON audit_events(project_id, created_at DESC);
"""
