"""Isolated additive SQLite schema for Workspace source V2."""

from __future__ import annotations

from .contracts import WORKSPACE_SCHEMA_VERSION, WorkspaceEntityType

WORKSPACE_ENTITY_TABLES = {
    WorkspaceEntityType.PROJECT: "workspace_projects_v2",
    WorkspaceEntityType.TOPIC: "workspace_topics_v2",
    WorkspaceEntityType.ITERATION: "workspace_iterations_v2",
    WorkspaceEntityType.WORK_ITEM: "workspace_work_items_v2",
    WorkspaceEntityType.ACCEPTANCE_CRITERION: "workspace_acceptance_criteria_v2",
    WorkspaceEntityType.ACCEPTANCE_CHECK: "workspace_acceptance_checks_v2",
    WorkspaceEntityType.DEPENDENCY: "workspace_dependencies_v2",
    WorkspaceEntityType.BLOCKER: "workspace_blockers_v2",
    WorkspaceEntityType.RISK: "workspace_risks_v2",
    WorkspaceEntityType.EVIDENCE_REQUIREMENT: "workspace_evidence_requirements_v2",
    WorkspaceEntityType.EVIDENCE_LINK: "workspace_evidence_links_v2",
    WorkspaceEntityType.DECISION: "workspace_decisions_v2",
    WorkspaceEntityType.OUTCOME: "workspace_outcomes_v2",
    WorkspaceEntityType.TRANSITION: "workspace_state_transitions_v2",
    WorkspaceEntityType.SNAPSHOT: "workspace_snapshots_v2",
    WorkspaceEntityType.INTELLIGENCE_RUN: "workspace_intelligence_runs_v2",
    WorkspaceEntityType.POLICY_VERSION: "workspace_policy_versions_v2",
}

WORKSPACE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS workspace_schema_meta_v2 (
        schema_version TEXT PRIMARY KEY,
        installed_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_publications_v2 (
        publication_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        acl_ref TEXT NOT NULL,
        generation_id TEXT NOT NULL,
        publication_sha256 TEXT NOT NULL,
        publication_json TEXT NOT NULL,
        published_at TEXT NOT NULL,
        active INTEGER NOT NULL CHECK (active IN (0, 1)),
        UNIQUE(project_id, generation_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_entities_v2 (
        entity_id TEXT PRIMARY KEY,
        stable_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        project_id TEXT NOT NULL,
        acl_ref TEXT NOT NULL,
        generation_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        display_key TEXT NOT NULL,
        label TEXT NOT NULL,
        status TEXT NOT NULL,
        authority TEXT NOT NULL,
        effective_at TEXT NOT NULL,
        current INTEGER NOT NULL CHECK (current IN (0, 1)),
        archived INTEGER NOT NULL CHECK (archived IN (0, 1)),
        content_sha256 TEXT NOT NULL,
        entity_json TEXT NOT NULL,
        publication_id TEXT NOT NULL REFERENCES workspace_publications_v2(publication_id),
        UNIQUE(project_id, stable_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_edges_v2 (
        edge_id TEXT PRIMARY KEY,
        edge_type TEXT NOT NULL,
        source_id TEXT NOT NULL REFERENCES workspace_entities_v2(entity_id),
        target_id TEXT NOT NULL REFERENCES workspace_entities_v2(entity_id),
        project_id TEXT NOT NULL,
        generation_id TEXT NOT NULL,
        reviewed INTEGER NOT NULL CHECK (reviewed IN (0, 1)),
        current INTEGER NOT NULL CHECK (current IN (0, 1)),
        content_sha256 TEXT NOT NULL,
        edge_json TEXT NOT NULL,
        publication_id TEXT NOT NULL REFERENCES workspace_publications_v2(publication_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_retrieval_units_v2 (
        unit_id TEXT PRIMARY KEY,
        entity_id TEXT NOT NULL REFERENCES workspace_entities_v2(entity_id),
        project_id TEXT NOT NULL,
        acl_ref TEXT NOT NULL,
        generation_id TEXT NOT NULL,
        unit_type TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        unit_json TEXT NOT NULL,
        publication_id TEXT NOT NULL REFERENCES workspace_publications_v2(publication_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_tombstones_v2 (
        tombstone_id TEXT PRIMARY KEY,
        target_entity_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        generation_id TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        tombstone_json TEXT NOT NULL,
        effective_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_workspace_entities_scope_v2
    ON workspace_entities_v2(project_id, generation_id, entity_type, status, current, archived)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_workspace_entities_key_v2
    ON workspace_entities_v2(project_id, display_key, stable_id, effective_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_workspace_edges_adjacency_v2
    ON workspace_edges_v2(project_id, generation_id, source_id, target_id, edge_type)
    """,
)


def workspace_typed_table_statement(table: str) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS {table} (
        entity_id TEXT PRIMARY KEY REFERENCES workspace_entities_v2(entity_id),
        project_id TEXT NOT NULL,
        generation_id TEXT NOT NULL,
        status TEXT NOT NULL,
        authority TEXT NOT NULL,
        content_sha256 TEXT NOT NULL
    )
    """


__all__ = [
    "WORKSPACE_ENTITY_TABLES",
    "WORKSPACE_SCHEMA_STATEMENTS",
    "WORKSPACE_SCHEMA_VERSION",
    "workspace_typed_table_statement",
]
