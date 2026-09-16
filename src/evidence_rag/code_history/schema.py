from __future__ import annotations

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS git_commits (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    sha TEXT NOT NULL,
    tree_hash TEXT,
    parent_shas_json TEXT NOT NULL DEFAULT '[]',
    author_name TEXT,
    author_email_hash TEXT,
    authored_at TEXT,
    committed_at TEXT,
    message TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(repository_id, sha),
    FOREIGN KEY(repository_id) REFERENCES repositories(id)
);
CREATE INDEX IF NOT EXISTS idx_git_commits_repo_time
    ON git_commits(repository_id, committed_at DESC);

CREATE TABLE IF NOT EXISTS git_branches (
    id TEXT PRIMARY KEY,
    repository_id TEXT NOT NULL,
    name TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    observed_at TEXT NOT NULL,
    UNIQUE(repository_id, name),
    FOREIGN KEY(repository_id) REFERENCES repositories(id)
);

CREATE TABLE IF NOT EXISTS diff_hunks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    commit_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    parent_sha TEXT,
    path TEXT NOT NULL,
    old_path TEXT,
    change_type TEXT NOT NULL,
    old_start INTEGER,
    old_count INTEGER,
    new_start INTEGER,
    new_count INTEGER,
    patch TEXT NOT NULL,
    patch_hash TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    affected_symbols_json TEXT NOT NULL DEFAULT '[]',
    raw_object_id TEXT,
    observed_at TEXT NOT NULL,
    FOREIGN KEY(repository_id) REFERENCES repositories(id),
    FOREIGN KEY(commit_id) REFERENCES git_commits(id)
);
CREATE INDEX IF NOT EXISTS idx_diff_hunks_commit
    ON diff_hunks(repository_id, commit_sha, path);
CREATE INDEX IF NOT EXISTS idx_diff_hunks_hash
    ON diff_hunks(repository_id, patch_hash);

CREATE TABLE IF NOT EXISTS test_results (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    commit_id TEXT,
    commit_sha TEXT NOT NULL,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    exit_code INTEGER,
    duration_ms REAL,
    stdout_ref TEXT,
    stderr_ref TEXT,
    framework TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    acl_ref TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY(repository_id) REFERENCES repositories(id)
);
CREATE INDEX IF NOT EXISTS idx_test_results_commit
    ON test_results(repository_id, commit_sha, observed_at DESC);
"""
