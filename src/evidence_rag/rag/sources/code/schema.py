"""Additive SQLite schema for rebuildable Code Source V2 retrieval data."""

CODE_V2_SCHEMA = r"""
CREATE UNIQUE INDEX IF NOT EXISTS idx_index_generations_code_v2_identity
    ON index_generations(id, repository_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_code_v2_identity
    ON entities(id, generation_id, repository_id, project_id, acl_ref);

CREATE TABLE IF NOT EXISTS code_retrieval_units (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL CHECK(length(id) > 0),
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    parent_unit_id TEXT,
    repository_id TEXT NOT NULL CHECK(length(repository_id) > 0),
    generation_id TEXT NOT NULL CHECK(length(generation_id) > 0),
    project_id TEXT NOT NULL CHECK(length(project_id) > 0),
    unit_type TEXT NOT NULL CHECK(length(unit_type) > 0),
    ast_node_type TEXT,
    ordinal INTEGER NOT NULL DEFAULT 0 CHECK(ordinal >= 0),
    language TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    qualified_name TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    identifiers TEXT NOT NULL DEFAULT '',
    doc TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    context_ref_json TEXT NOT NULL DEFAULT '{}'
        CHECK(json_valid(context_ref_json)),
    start_line INTEGER CHECK(start_line IS NULL OR start_line >= 1),
    end_line INTEGER CHECK(end_line IS NULL OR end_line >= 1),
    start_byte INTEGER CHECK(start_byte IS NULL OR start_byte >= 0),
    end_byte INTEGER CHECK(end_byte IS NULL OR end_byte >= 0),
    token_count INTEGER NOT NULL DEFAULT 0 CHECK(token_count >= 0),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    builder_version TEXT NOT NULL CHECK(length(builder_version) > 0),
    quality_status TEXT NOT NULL DEFAULT 'ready' CHECK(length(quality_status) > 0),
    acl_ref TEXT NOT NULL CHECK(length(acl_ref) > 0),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(id, generation_id),
    CHECK(start_line IS NULL OR end_line IS NULL OR end_line >= start_line),
    CHECK(start_byte IS NULL OR end_byte IS NULL OR end_byte >= start_byte),
    FOREIGN KEY(repository_id)
        REFERENCES repositories(id) ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY(generation_id, repository_id)
        REFERENCES index_generations(id, repository_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY(entity_id, generation_id, repository_id, project_id, acl_ref)
        REFERENCES entities(id, generation_id, repository_id, project_id, acl_ref)
        ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY(parent_unit_id, generation_id)
        REFERENCES code_retrieval_units(id, generation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_code_units_scope
    ON code_retrieval_units(
        project_id, repository_id, generation_id, acl_ref, unit_type, language
    );
CREATE INDEX IF NOT EXISTS idx_code_units_entity
    ON code_retrieval_units(entity_id, generation_id);
CREATE INDEX IF NOT EXISTS idx_code_units_parent
    ON code_retrieval_units(parent_unit_id, generation_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_code_units_exact_name
    ON code_retrieval_units(repository_id, generation_id, qualified_name);
CREATE INDEX IF NOT EXISTS idx_code_units_exact_path
    ON code_retrieval_units(repository_id, generation_id, path, start_line);

CREATE VIRTUAL TABLE IF NOT EXISTS code_retrieval_units_fts USING fts5(
    qualified_name,
    signature,
    path,
    identifiers,
    doc,
    body,
    content='code_retrieval_units',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS code_retrieval_units_ai
AFTER INSERT ON code_retrieval_units BEGIN
    INSERT INTO code_retrieval_units_fts(
        rowid, qualified_name, signature, path, identifiers, doc, body
    ) VALUES (
        new.rowid, new.qualified_name, new.signature, new.path,
        new.identifiers, new.doc, new.body
    );
END;

CREATE TRIGGER IF NOT EXISTS code_retrieval_units_ad
AFTER DELETE ON code_retrieval_units BEGIN
    INSERT INTO code_retrieval_units_fts(
        code_retrieval_units_fts, rowid, qualified_name, signature, path,
        identifiers, doc, body
    ) VALUES (
        'delete', old.rowid, old.qualified_name, old.signature, old.path,
        old.identifiers, old.doc, old.body
    );
END;

CREATE TRIGGER IF NOT EXISTS code_retrieval_units_au
AFTER UPDATE ON code_retrieval_units BEGIN
    INSERT INTO code_retrieval_units_fts(
        code_retrieval_units_fts, rowid, qualified_name, signature, path,
        identifiers, doc, body
    ) VALUES (
        'delete', old.rowid, old.qualified_name, old.signature, old.path,
        old.identifiers, old.doc, old.body
    );
    INSERT INTO code_retrieval_units_fts(
        rowid, qualified_name, signature, path, identifiers, doc, body
    ) VALUES (
        new.rowid, new.qualified_name, new.signature, new.path,
        new.identifiers, new.doc, new.body
    );
END;

CREATE TABLE IF NOT EXISTS code_unit_vectors (
    unit_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    profile TEXT NOT NULL CHECK(length(profile) > 0),
    model TEXT NOT NULL CHECK(length(model) > 0),
    dimension INTEGER NOT NULL CHECK(dimension > 0),
    vector BLOB NOT NULL CHECK(length(vector) = dimension * 4),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(unit_id, generation_id, profile, model),
    FOREIGN KEY(unit_id, generation_id)
        REFERENCES code_retrieval_units(id, generation_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_code_unit_vectors_pool
    ON code_unit_vectors(generation_id, profile, model);

CREATE TABLE IF NOT EXISTS code_unit_embedding_cache (
    cache_key TEXT NOT NULL CHECK(length(cache_key) > 0),
    profile TEXT NOT NULL CHECK(length(profile) > 0),
    model TEXT NOT NULL CHECK(length(model) > 0),
    dimension INTEGER NOT NULL CHECK(dimension > 0),
    vector BLOB NOT NULL CHECK(length(vector) = dimension * 4),
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    PRIMARY KEY(cache_key, profile, model)
);
CREATE INDEX IF NOT EXISTS idx_code_embedding_cache_usage
    ON code_unit_embedding_cache(profile, model, last_used_at);

CREATE TABLE IF NOT EXISTS code_relation_diagnostics (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL CHECK(length(id) > 0),
    repository_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    source_entity_id TEXT NOT NULL CHECK(length(source_entity_id) > 0),
    relation_type TEXT NOT NULL CHECK(length(relation_type) > 0),
    raw_target TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(length(reason) > 0),
    candidates_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(candidates_json)),
    parser TEXT NOT NULL CHECK(length(parser) > 0),
    acl_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(id, generation_id),
    FOREIGN KEY(repository_id)
        REFERENCES repositories(id) ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY(generation_id, repository_id)
        REFERENCES index_generations(id, repository_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY(source_entity_id, generation_id, repository_id, project_id, acl_ref)
        REFERENCES entities(id, generation_id, repository_id, project_id, acl_ref)
        ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_code_relation_diagnostics_scope
    ON code_relation_diagnostics(
        project_id, repository_id, generation_id, relation_type, reason
    );

CREATE TABLE IF NOT EXISTS code_retrieval_calibrations (
    id TEXT PRIMARY KEY CHECK(length(id) > 0),
    profile TEXT NOT NULL CHECK(length(profile) > 0),
    task TEXT NOT NULL CHECK(length(task) > 0),
    entity_type TEXT NOT NULL CHECK(length(entity_type) > 0),
    model TEXT NOT NULL CHECK(length(model) > 0),
    artifact_ref TEXT NOT NULL CHECK(length(artifact_ref) > 0),
    metrics_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metrics_json)),
    status TEXT NOT NULL CHECK(length(status) > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(profile, task, entity_type, model, artifact_ref)
);
CREATE INDEX IF NOT EXISTS idx_code_calibrations_lookup
    ON code_retrieval_calibrations(profile, task, entity_type, model, status);

CREATE TABLE IF NOT EXISTS code_index_publications (
    generation_id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    builder TEXT NOT NULL CHECK(length(builder) > 0),
    sparse TEXT NOT NULL CHECK(length(sparse) > 0),
    embedding TEXT NOT NULL CHECK(length(embedding) > 0),
    graph TEXT NOT NULL CHECK(length(graph) > 0),
    status TEXT NOT NULL CHECK(length(status) > 0),
    validation_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(validation_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(generation_id, repository_id)
        REFERENCES index_generations(id, repository_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY(repository_id)
        REFERENCES repositories(id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_code_publications_scope
    ON code_index_publications(project_id, repository_id, status, created_at);
"""


__all__ = ["CODE_V2_SCHEMA"]
