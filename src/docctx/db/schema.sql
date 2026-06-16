-- docctx database schema
-- SQLite 3.x with FTS5 required

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;
PRAGMA cache_size = -32000;

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    notes       TEXT
);

-- Context packs (user-defined retrieval scopes)
CREATE TABLE IF NOT EXISTS packs (
    name            TEXT PRIMARY KEY,
    entry_url       TEXT NOT NULL,
    scope_rule      TEXT NOT NULL DEFAULT 'siblings',
    trust_tier      INTEGER NOT NULL DEFAULT 1,
    version_tag     TEXT,
    last_refreshed  TEXT,
    doc_count       INTEGER NOT NULL DEFAULT 0,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Fetched and extracted pages (source of truth)
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    url             TEXT NOT NULL,
    pack_name       TEXT NOT NULL REFERENCES packs(name) ON DELETE CASCADE,
    content_hash    TEXT NOT NULL,
    raw_markdown    TEXT NOT NULL,
    title           TEXT,
    fetch_status    TEXT NOT NULL DEFAULT 'ok',
    fetched_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(url, pack_name)
);

CREATE INDEX IF NOT EXISTS idx_documents_pack ON documents(pack_name);

-- Derived retrieval units (regenerable from documents)
CREATE TABLE IF NOT EXISTS chunks (
    id              TEXT PRIMARY KEY,    -- deterministic hash-based ID
    pack_name       TEXT NOT NULL REFERENCES packs(name) ON DELETE CASCADE,
    doc_url         TEXT NOT NULL,
    heading_path    TEXT NOT NULL,
    heading_title   TEXT NOT NULL,
    content         TEXT NOT NULL,
    summary         TEXT NOT NULL,
    content_preview TEXT NOT NULL,
    code_content    TEXT NOT NULL DEFAULT '',
    token_count     INTEGER NOT NULL DEFAULT 0,
    chunk_index     INTEGER NOT NULL DEFAULT 0,
    trust_tier      INTEGER NOT NULL DEFAULT 1,
    prev_chunk_id   TEXT,
    next_chunk_id   TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_pack ON chunks(pack_name);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_url ON chunks(doc_url);

-- FTS5 virtual table for full-text search
-- Column weights: heading_path=1.5, heading_title=1.5, content=1.0, code_content=0.5
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    id UNINDEXED,
    pack_name UNINDEXED,
    heading_path,
    heading_title,
    content,
    code_content,
    content=chunks,
    content_rowid=rowid,
    tokenize='unicode61'
);

-- Triggers to keep FTS5 in sync with chunks table
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, id, pack_name, heading_path, heading_title, content, code_content)
    VALUES (new.rowid, new.id, new.pack_name,
            normalize_text(new.heading_path),
            normalize_text(new.heading_title),
            normalize_text(new.content),
            normalize_text(new.code_content));
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, id, pack_name, heading_path, heading_title, content, code_content)
    VALUES ('delete', old.rowid, old.id, old.pack_name,
            normalize_text(old.heading_path),
            normalize_text(old.heading_title),
            normalize_text(old.content),
            normalize_text(old.code_content));
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, id, pack_name, heading_path, heading_title, content, code_content)
    VALUES ('delete', old.rowid, old.id, old.pack_name,
            normalize_text(old.heading_path),
            normalize_text(old.heading_title),
            normalize_text(old.content),
            normalize_text(old.code_content));
    INSERT INTO chunks_fts(rowid, id, pack_name, heading_path, heading_title, content, code_content)
    VALUES (new.rowid, new.id, new.pack_name,
            normalize_text(new.heading_path),
            normalize_text(new.heading_title),
            normalize_text(new.content),
            normalize_text(new.code_content));
END;

-- Future-proof embeddings stub (empty in M1)
CREATE TABLE IF NOT EXISTS embeddings (
    chunk_id    TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Phase 2 stub: chunk cross-references
CREATE TABLE IF NOT EXISTS chunk_relations (
    from_chunk_id   TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    to_chunk_id     TEXT NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    relation_type   TEXT NOT NULL,  -- 'see_also' | 'child_of' | 'example_of'
    weight          REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (from_chunk_id, to_chunk_id, relation_type)
);

-- Insert initial schema version
INSERT OR IGNORE INTO schema_version(version, notes) VALUES (1, 'M1 initial schema');
