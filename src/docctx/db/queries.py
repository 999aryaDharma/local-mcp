"""
Database query functions — CRUD operations for packs, documents, chunks.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Optional

from docctx.models import Chunk, Document, Pack


# ── Packs ─────────────────────────────────────────────────────────────────────


def insert_pack(conn: sqlite3.Connection, pack: Pack) -> None:
    conn.execute(
        """
        INSERT INTO packs (name, entry_url, scope_rule, trust_tier, version_tag, last_refreshed)
        VALUES (:name, :entry_url, :scope_rule, :trust_tier, :version_tag, :last_refreshed)
        """,
        {
            "name": pack.name,
            "entry_url": pack.entry_url,
            "scope_rule": pack.scope_rule,
            "trust_tier": pack.trust_tier,
            "version_tag": pack.version_tag,
            "last_refreshed": _dt(pack.last_refreshed),
        },
    )


def update_pack_stats(
    conn: sqlite3.Connection,
    pack_name: str,
    doc_count: int,
    chunk_count: int,
    last_refreshed: datetime,
) -> None:
    conn.execute(
        """
        UPDATE packs
        SET doc_count = :doc_count,
            chunk_count = :chunk_count,
            last_refreshed = :last_refreshed
        WHERE name = :name
        """,
        {
            "name": pack_name,
            "doc_count": doc_count,
            "chunk_count": chunk_count,
            "last_refreshed": _dt(last_refreshed),
        },
    )


def get_pack(conn: sqlite3.Connection, name: str) -> Optional[Pack]:
    row = conn.execute("SELECT * FROM packs WHERE name = ?", (name,)).fetchone()
    return _row_to_pack(row) if row else None


def list_packs(conn: sqlite3.Connection, name_pattern: Optional[str] = None) -> list[Pack]:
    if name_pattern:
        # Convert glob to SQL LIKE pattern
        like_pattern = name_pattern.replace("*", "%").replace("?", "_")
        rows = conn.execute(
            "SELECT * FROM packs WHERE name LIKE ? ORDER BY name", (like_pattern,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM packs ORDER BY name").fetchall()
    return [_row_to_pack(r) for r in rows]


def pack_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM packs WHERE name = ? LIMIT 1", (name,)
    ).fetchone()
    return row is not None


def delete_pack(conn: sqlite3.Connection, name: str) -> None:
    conn.execute("DELETE FROM packs WHERE name = ?", (name,))


# ── Documents ─────────────────────────────────────────────────────────────────


def insert_document(conn: sqlite3.Connection, doc: Document) -> int:
    cursor = conn.execute(
        """
        INSERT INTO documents (url, pack_name, content_hash, raw_markdown, title, fetch_status, fetched_at)
        VALUES (:url, :pack_name, :content_hash, :raw_markdown, :title, :fetch_status, :fetched_at)
        ON CONFLICT(url, pack_name) DO UPDATE SET
            content_hash = excluded.content_hash,
            raw_markdown = excluded.raw_markdown,
            title = excluded.title,
            fetch_status = excluded.fetch_status,
            fetched_at = excluded.fetched_at
        """,
        {
            "url": doc.url,
            "pack_name": doc.pack_name,
            "content_hash": doc.content_hash,
            "raw_markdown": doc.raw_markdown,
            "title": doc.title,
            "fetch_status": doc.fetch_status,
            "fetched_at": _dt(doc.fetched_at) or _now(),
        },
    )
    return cursor.lastrowid  # type: ignore[return-value]


def get_document(
    conn: sqlite3.Connection, url: str, pack_name: str
) -> Optional[Document]:
    row = conn.execute(
        "SELECT * FROM documents WHERE url = ? AND pack_name = ?", (url, pack_name)
    ).fetchone()
    return _row_to_document(row) if row else None


def list_documents(conn: sqlite3.Connection, pack_name: str) -> list[Document]:
    rows = conn.execute(
        "SELECT * FROM documents WHERE pack_name = ? ORDER BY url", (pack_name,)
    ).fetchall()
    return [_row_to_document(r) for r in rows]


def delete_chunks_for_document(
    conn: sqlite3.Connection, doc_url: str, pack_name: str
) -> None:
    conn.execute(
        "DELETE FROM chunks WHERE doc_url = ? AND pack_name = ?", (doc_url, pack_name)
    )


# ── Chunks ────────────────────────────────────────────────────────────────────


def insert_chunk(conn: sqlite3.Connection, chunk: Chunk) -> None:
    cursor = conn.execute(
        """
        INSERT OR REPLACE INTO chunks
            (id, pack_name, doc_url, heading_path, heading_title,
             content, summary, llm_summary, content_preview, code_content,
             token_count, chunk_index, trust_tier, prev_chunk_id, next_chunk_id)
        VALUES
            (:id, :pack_name, :doc_url, :heading_path, :heading_title,
             :content, :summary, :llm_summary, :content_preview, :code_content,
             :token_count, :chunk_index, :trust_tier, :prev_chunk_id, :next_chunk_id)
        """,
        {
            "id": chunk.id,
            "pack_name": chunk.pack_name,
            "doc_url": chunk.doc_url,
            "heading_path": chunk.heading_path,
            "heading_title": chunk.heading_title,
            "content": chunk.content,
            "summary": chunk.summary,
            "llm_summary": chunk.llm_summary,
            "content_preview": chunk.content_preview,
            "code_content": chunk.code_content,
            "token_count": chunk.token_count,
            "chunk_index": chunk.chunk_index,
            "trust_tier": chunk.trust_tier,
            "prev_chunk_id": chunk.prev_chunk_id,
            "next_chunk_id": chunk.next_chunk_id,
        },
    )

    if chunk.embedding is not None:
        import sqlite_vec
        conn.execute(
            "INSERT INTO chunks_vec(rowid, embedding) VALUES (?, ?)",
            (cursor.lastrowid, sqlite_vec.serialize_float32(chunk.embedding))
        )
        
    for relation in getattr(chunk, "extracted_relations", []):
        conn.execute(
            """
            INSERT OR IGNORE INTO concept_edges (chunk_id, target_concept, relation_type)
            VALUES (?, ?, ?)
            """,
            (chunk.id, relation["target"], relation["type"])
        )


def get_chunk_by_id(conn: sqlite3.Connection, chunk_id: str) -> Optional[Chunk]:
    row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    return _row_to_chunk(row) if row else None


def count_chunks(conn: sqlite3.Connection, pack_name: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) as n FROM chunks WHERE pack_name = ?", (pack_name,)
    ).fetchone()
    return row["n"] if row else 0


def count_documents(conn: sqlite3.Connection, pack_name: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) as n FROM documents WHERE pack_name = ?", (pack_name,)
    ).fetchone()
    return row["n"] if row else 0


# ── Helpers ───────────────────────────────────────────────────────────────────


def _dt(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _row_to_pack(row: sqlite3.Row) -> Pack:
    return Pack(
        name=row["name"],
        entry_url=row["entry_url"],
        scope_rule=row["scope_rule"],
        trust_tier=row["trust_tier"],
        version_tag=row["version_tag"],
        last_refreshed=_parse_dt(row["last_refreshed"]),
        doc_count=row["doc_count"],
        chunk_count=row["chunk_count"],
        created_at=_parse_dt(row["created_at"]),
    )


def _row_to_document(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        url=row["url"],
        pack_name=row["pack_name"],
        content_hash=row["content_hash"],
        raw_markdown=row["raw_markdown"],
        title=row["title"],
        fetch_status=row["fetch_status"],
        fetched_at=_parse_dt(row["fetched_at"]),
    )


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    # Build relations if present
    # Concept relations are loaded separately in get_chunk via service.
    return Chunk(
        id=row["id"],
        pack_name=row["pack_name"],
        doc_url=row["doc_url"],
        heading_path=row["heading_path"],
        heading_title=row["heading_title"],
        content=row["content"],
        summary=row["summary"],
        llm_summary=row.get("llm_summary"),
        content_preview=row["content_preview"],
        code_content=row["code_content"],
        token_count=row["token_count"],
        chunk_index=row["chunk_index"],
        trust_tier=row["trust_tier"],
        prev_chunk_id=row["prev_chunk_id"],
        next_chunk_id=row["next_chunk_id"],
    )


def run_optimize(conn: sqlite3.Connection) -> None:
    """
    Update SQLite query planner statistics dan FTS5 term stats.
    Jalankan setelah bulk ingestion atau via `docctx doctor --optimize`.
    """
    conn.execute("PRAGMA optimize")
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    conn.commit()
