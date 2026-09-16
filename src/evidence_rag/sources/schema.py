from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS raw_objects (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_instance TEXT NOT NULL,
    source_object_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    source_uri TEXT,
    content_hash TEXT NOT NULL,
    media_type TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    storage_path TEXT,
    acl_ref TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    adapter_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    observed_at TEXT NOT NULL,
    tombstoned_at TEXT,
    UNIQUE(source_instance, source_object_id, source_version, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_raw_source_object
    ON raw_objects(project_id, source_type, source_object_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_state
    ON raw_objects(project_id, state, observed_at DESC);

CREATE TABLE IF NOT EXISTS source_events (
    event_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    source_instance TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_object_id TEXT NOT NULL,
    source_version TEXT NOT NULL,
    event_time TEXT,
    observed_at TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_object_id TEXT,
    payload_ref TEXT,
    trace_id TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(raw_object_id) REFERENCES raw_objects(id)
);
CREATE INDEX IF NOT EXISTS idx_source_events_project
    ON source_events(project_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_events_object
    ON source_events(source_instance, source_object_id, source_version);

CREATE TABLE IF NOT EXISTS raw_derivations (
    raw_object_id TEXT NOT NULL,
    derived_entity_id TEXT NOT NULL,
    derived_kind TEXT NOT NULL,
    generation_id TEXT,
    derivation_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    invalidated_at TEXT,
    PRIMARY KEY(raw_object_id, derived_entity_id, derivation_version),
    FOREIGN KEY(raw_object_id) REFERENCES raw_objects(id)
);
CREATE INDEX IF NOT EXISTS idx_raw_derivations_entity
    ON raw_derivations(derived_entity_id, invalidated_at);

CREATE TABLE IF NOT EXISTS blocked_entities (
    entity_id TEXT PRIMARY KEY,
    raw_object_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    blocked_at TEXT NOT NULL,
    FOREIGN KEY(raw_object_id) REFERENCES raw_objects(id)
);
"""
