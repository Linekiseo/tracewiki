from __future__ import annotations

BASE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS repositories (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    local_path TEXT NOT NULL,
    default_branch TEXT,
    head_commit TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    active_generation_id TEXT,
    stats_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    repository_id TEXT,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    request_json TEXT NOT NULL,
    counters_json TEXT NOT NULL DEFAULT '{}',
    generation_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_generations (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    status TEXT NOT NULL,
    parser_versions_json TEXT NOT NULL DEFAULT '{}',
    counts_json TEXT NOT NULL DEFAULT '{}',
    validation_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    FOREIGN KEY(repository_id) REFERENCES repositories(id)
);

CREATE TABLE IF NOT EXISTS entities (
    entity_key INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT,
    path TEXT,
    language TEXT,
    commit_sha TEXT NOT NULL,
    blob_hash TEXT,
    content_hash TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    source_uri TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(id, generation_id),
    FOREIGN KEY(repository_id) REFERENCES repositories(id),
    FOREIGN KEY(generation_id) REFERENCES index_generations(id)
);

CREATE INDEX IF NOT EXISTS idx_entities_active
    ON entities(repository_id, generation_id, entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_path
    ON entities(repository_id, generation_id, path);
CREATE INDEX IF NOT EXISTS idx_entities_name
    ON entities(repository_id, generation_id, name);
CREATE INDEX IF NOT EXISTS idx_entities_project_path
    ON entities(project_id, path, generation_id, entity_type, start_line);
CREATE INDEX IF NOT EXISTS idx_entities_project_qualified_name
    ON entities(project_id, qualified_name, generation_id);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    derivation TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_locator TEXT,
    PRIMARY KEY(id, generation_id),
    FOREIGN KEY(repository_id) REFERENCES repositories(id),
    FOREIGN KEY(generation_id) REFERENCES index_generations(id)
);

CREATE INDEX IF NOT EXISTS idx_edges_source
    ON edges(generation_id, source_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_edges_target
    ON edges(generation_id, target_id, edge_type);

CREATE TABLE IF NOT EXISTS search_views (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    view_type TEXT NOT NULL,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT NOT NULL,
    content TEXT NOT NULL,
    vector BLOB NOT NULL,
    embedding_model TEXT NOT NULL,
    UNIQUE(id, generation_id),
    FOREIGN KEY(repository_id) REFERENCES repositories(id),
    FOREIGN KEY(generation_id) REFERENCES index_generations(id)
);

CREATE INDEX IF NOT EXISTS idx_views_active
    ON search_views(repository_id, generation_id, language, view_type);

CREATE VIRTUAL TABLE IF NOT EXISTS search_views_fts USING fts5(
    name,
    path,
    content,
    content='search_views',
    content_rowid='rowid',
    tokenize='unicode61'
);

CREATE TRIGGER IF NOT EXISTS search_views_ai AFTER INSERT ON search_views BEGIN
    INSERT INTO search_views_fts(rowid, name, path, content)
    VALUES (new.rowid, new.name, new.path, new.content);
END;
CREATE TRIGGER IF NOT EXISTS search_views_ad AFTER DELETE ON search_views BEGIN
    INSERT INTO search_views_fts(search_views_fts, rowid, name, path, content)
    VALUES ('delete', old.rowid, old.name, old.path, old.content);
END;
CREATE TRIGGER IF NOT EXISTS search_views_au AFTER UPDATE ON search_views BEGIN
    INSERT INTO search_views_fts(search_views_fts, rowid, name, path, content)
    VALUES ('delete', old.rowid, old.name, old.path, old.content);
    INSERT INTO search_views_fts(rowid, name, path, content)
    VALUES (new.rowid, new.name, new.path, new.content);
END;

CREATE TABLE IF NOT EXISTS codex_sources (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    project_path TEXT,
    acl_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    active_generation_id TEXT,
    stats_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS codex_generations (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    counts_json TEXT NOT NULL DEFAULT '{}',
    validation_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    completed_at TEXT,
    error TEXT,
    FOREIGN KEY(source_id) REFERENCES codex_sources(id)
);

CREATE TABLE IF NOT EXISTS codex_threads (
    thread_key INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    cwd TEXT,
    status TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT,
    source_file TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(id, source_id, generation_id),
    FOREIGN KEY(source_id) REFERENCES codex_sources(id),
    FOREIGN KEY(generation_id) REFERENCES codex_generations(id)
);
CREATE INDEX IF NOT EXISTS idx_codex_threads_active
    ON codex_threads(source_id, generation_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_codex_threads_raw_id
    ON codex_threads(thread_id);

CREATE TABLE IF NOT EXISTS codex_turns (
    turn_key INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    goal TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(id, source_id, generation_id),
    FOREIGN KEY(source_id) REFERENCES codex_sources(id),
    FOREIGN KEY(generation_id) REFERENCES codex_generations(id)
);
CREATE INDEX IF NOT EXISTS idx_codex_turns_thread
    ON codex_turns(source_id, generation_id, thread_id, ordinal);

CREATE TABLE IF NOT EXISTS codex_items (
    item_key INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    role TEXT,
    status TEXT,
    timestamp TEXT,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(id, source_id, generation_id),
    FOREIGN KEY(source_id) REFERENCES codex_sources(id),
    FOREIGN KEY(generation_id) REFERENCES codex_generations(id)
);
CREATE INDEX IF NOT EXISTS idx_codex_items_thread
    ON codex_items(source_id, generation_id, thread_id, sequence);
CREATE INDEX IF NOT EXISTS idx_codex_items_turn
    ON codex_items(source_id, generation_id, turn_id, sequence);
CREATE INDEX IF NOT EXISTS idx_codex_items_type
    ON codex_items(source_id, generation_id, item_type);

CREATE TABLE IF NOT EXISTS codex_edges (
    id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    derivation TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_locator TEXT,
    PRIMARY KEY(id, generation_id),
    FOREIGN KEY(source_id) REFERENCES codex_sources(id),
    FOREIGN KEY(generation_id) REFERENCES codex_generations(id)
);
CREATE INDEX IF NOT EXISTS idx_codex_edges_source
    ON codex_edges(generation_id, source_entity_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_codex_edges_target
    ON codex_edges(generation_id, target_entity_id, edge_type);

CREATE TABLE IF NOT EXISTS codex_search_views (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    view_type TEXT NOT NULL,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    vector BLOB NOT NULL,
    embedding_model TEXT NOT NULL,
    UNIQUE(id, source_id, generation_id),
    FOREIGN KEY(source_id) REFERENCES codex_sources(id),
    FOREIGN KEY(generation_id) REFERENCES codex_generations(id)
);
CREATE INDEX IF NOT EXISTS idx_codex_views_active
    ON codex_search_views(source_id, generation_id, thread_id, view_type);

CREATE VIRTUAL TABLE IF NOT EXISTS codex_search_views_fts USING fts5(
    name,
    content,
    content='codex_search_views',
    content_rowid='rowid',
    tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS codex_search_views_ai
AFTER INSERT ON codex_search_views BEGIN
    INSERT INTO codex_search_views_fts(rowid, name, content)
    VALUES (new.rowid, new.name, new.content);
END;
CREATE TRIGGER IF NOT EXISTS codex_search_views_ad
AFTER DELETE ON codex_search_views BEGIN
    INSERT INTO codex_search_views_fts(
        codex_search_views_fts, rowid, name, content
    ) VALUES ('delete', old.rowid, old.name, old.content);
END;
CREATE TRIGGER IF NOT EXISTS codex_search_views_au
AFTER UPDATE ON codex_search_views BEGIN
    INSERT INTO codex_search_views_fts(
        codex_search_views_fts, rowid, name, content
    ) VALUES ('delete', old.rowid, old.name, old.content);
    INSERT INTO codex_search_views_fts(rowid, name, content)
    VALUES (new.rowid, new.name, new.content);
END;
"""
