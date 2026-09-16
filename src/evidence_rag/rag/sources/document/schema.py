"""Additive schema owned exclusively by the isolated Document V2 store."""

from __future__ import annotations

DOCUMENT_V2_ENTITY_TABLES = {
    "page": "document_pages_v2",
    "layout_block": "document_layout_blocks_v2",
    "section": "document_sections_v2",
    "paragraph": "document_paragraphs_v2",
    "claim_candidate": "document_claim_candidates_v2",
    "claim": "document_claim_versions_v2",
    "table": "document_tables_v2",
    "table_row": "document_table_rows_v2",
    "table_cell_fact": "document_table_cell_facts_v2",
    "figure": "document_figures_v2",
    "formula": "document_formulas_v2",
    "reference_work": "document_reference_works_v2",
    "citation_mention": "document_citation_mentions_v2",
    "section_summary": "document_section_summaries_v2",
    "version_diff": "document_version_diffs_v2",
}

_ENTITY_TABLE_SQL = "\n".join(
    f"""
CREATE TABLE IF NOT EXISTS {table_name} (
    entity_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    typed_json TEXT NOT NULL,
    FOREIGN KEY(entity_id) REFERENCES document_entities_v2(entity_id),
    FOREIGN KEY(version_id) REFERENCES document_versions_v2(version_id)
);
"""
    for table_name in DOCUMENT_V2_ENTITY_TABLES.values()
)

DOCUMENT_V2_SCHEMA = (
    r"""
CREATE TABLE IF NOT EXISTS document_families_v2 (
    family_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    source_key TEXT NOT NULL,
    canonical_title TEXT NOT NULL,
    locator TEXT NOT NULL,
    latest_version_id TEXT,
    UNIQUE(project_id, acl_ref, source_key)
);

CREATE TABLE IF NOT EXISTS document_versions_v2 (
    version_id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    version_label TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    parse_quality TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    source_text TEXT NOT NULL,
    locator TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    FOREIGN KEY(family_id) REFERENCES document_families_v2(family_id),
    UNIQUE(family_id, generation_id, version_label, content_sha256)
);

CREATE TABLE IF NOT EXISTS document_entities_v2 (
    entity_id TEXT PRIMARY KEY,
    stable_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    family_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    parent_id TEXT,
    ordinal INTEGER NOT NULL,
    page_number INTEGER,
    source_text TEXT NOT NULL,
    derived_text TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    locator TEXT NOT NULL,
    authority TEXT NOT NULL,
    status TEXT NOT NULL,
    entity_json TEXT NOT NULL,
    FOREIGN KEY(family_id) REFERENCES document_families_v2(family_id),
    FOREIGN KEY(version_id) REFERENCES document_versions_v2(version_id),
    UNIQUE(version_id, stable_id, entity_type)
);
CREATE INDEX IF NOT EXISTS idx_document_entities_v2_scope
    ON document_entities_v2(project_id, acl_ref, generation_id, entity_type);

CREATE TABLE IF NOT EXISTS document_retrieval_units_v2 (
    unit_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    exact_keys_json TEXT NOT NULL,
    sparse_text TEXT NOT NULL,
    dense_source_text TEXT NOT NULL,
    dense_derived_text TEXT NOT NULL,
    source_text_sha256 TEXT NOT NULL,
    derived_text_sha256 TEXT NOT NULL,
    builder_version TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(version_id) REFERENCES document_versions_v2(version_id),
    FOREIGN KEY(entity_id) REFERENCES document_entities_v2(entity_id)
);

CREATE TABLE IF NOT EXISTS document_edges_v2 (
    edge_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    authority TEXT NOT NULL,
    confidence REAL NOT NULL,
    review_status TEXT NOT NULL,
    locator TEXT NOT NULL,
    FOREIGN KEY(version_id) REFERENCES document_versions_v2(version_id)
);
CREATE INDEX IF NOT EXISTS idx_document_edges_v2_source
    ON document_edges_v2(project_id, acl_ref, generation_id, source_id, edge_type);

CREATE TABLE IF NOT EXISTS document_publications_v2 (
    publication_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    family_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    source_payload_sha256 TEXT NOT NULL,
    publication_sha256 TEXT NOT NULL,
    publication_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(family_id) REFERENCES document_families_v2(family_id),
    FOREIGN KEY(version_id) REFERENCES document_versions_v2(version_id)
);

CREATE TABLE IF NOT EXISTS document_tombstones_v2 (
    tombstone_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    acl_ref TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    family_id TEXT NOT NULL,
    version_id TEXT,
    reason TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, acl_ref, generation_id, family_id, version_id)
);
"""
    + _ENTITY_TABLE_SQL
)

DOCUMENT_V2_TABLES = (
    "document_families_v2",
    "document_versions_v2",
    "document_entities_v2",
    *DOCUMENT_V2_ENTITY_TABLES.values(),
    "document_retrieval_units_v2",
    "document_edges_v2",
    "document_publications_v2",
    "document_tombstones_v2",
)
