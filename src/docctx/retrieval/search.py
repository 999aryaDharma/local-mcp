"""
FTS5 search — two-phase query (phrase match → OR fallback) against chunks_fts.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from docctx.db.connection import normalize_text
from docctx.models import Chunk
from docctx.retrieval.tokenizer import build_fts5_query

logger = logging.getLogger(__name__)

# BM25 column weights matching schema column order:
# (id, pack_name, heading_path, heading_title, content, code_content)
# Weights: heading_path=1.5, heading_title=1.5, content=1.0, code_content=0.5
# Note: UNINDEXED columns don't count in BM25 weight array
# FTS5 bm25() uses negative scores (more negative = better match)
_BM25_WEIGHTS = "1.0, 1.5, 1.5, 1.0, 0.5"


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    pack_filter: Optional[str] = None,
    top_k: int = 20,
) -> list[tuple[Chunk, float]]:
    """
    Two-phase FTS5 search:
    Phase 1: Phrase match ("useEffect cleanup") — higher precision
    Phase 2: OR match (useEffect OR cleanup) — fallback if < 5 phrase results

    Returns list of (Chunk, score) sorted by score descending.
    """
    phrase_query, or_query = build_fts5_query(query)

    # Phase 1: Phrase search
    results = _run_fts_query(conn, phrase_query, pack_filter, top_k)

    # Phase 2: OR fallback if insufficient results
    if len(results) < 5:
        or_results = _run_fts_query(conn, or_query, pack_filter, top_k)
        # Merge: deduplicate by chunk ID, keeping better score
        existing_ids = {chunk.id for chunk, _ in results}
        for chunk, score in or_results:
            if chunk.id not in existing_ids:
                results.append((chunk, score))
                existing_ids.add(chunk.id)

    # Sort by score descending
    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def _run_fts_query(
    conn: sqlite3.Connection,
    fts_query: str,
    pack_filter: Optional[str],
    top_k: int,
) -> list[tuple[Chunk, float]]:
    """Execute a single FTS5 query and return (Chunk, score) pairs."""
    try:
        if pack_filter:
            # Convert glob to SQL LIKE for pack filtering
            like_pattern = pack_filter.replace("*", "%").replace("?", "_")
            sql = """
                SELECT c.*, (-bm25(chunks_fts, {weights})) AS score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.rowid
                WHERE chunks_fts MATCH ?
                  AND c.pack_name LIKE ?
                ORDER BY score DESC
                LIMIT ?
            """.format(weights=_BM25_WEIGHTS)
            rows = conn.execute(sql, (fts_query, like_pattern, top_k)).fetchall()
        else:
            sql = """
                SELECT c.*, (-bm25(chunks_fts, {weights})) AS score
                FROM chunks_fts
                JOIN chunks c ON chunks_fts.rowid = c.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY score DESC
                LIMIT ?
            """.format(weights=_BM25_WEIGHTS)
            rows = conn.execute(sql, (fts_query, top_k)).fetchall()

        results = []
        for row in rows:
            chunk = _row_to_chunk(row)
            score = float(row["score"])
            results.append((chunk, score))
        return results

    except sqlite3.OperationalError as e:
        # FTS5 query syntax error — fall back to empty
        logger.warning("FTS5 query error for '%s': %s", fts_query, e)
        return []


def _row_to_chunk(row: sqlite3.Row) -> Chunk:
    from docctx.models import Chunk
    return Chunk(
        id=row["id"],
        pack_name=row["pack_name"],
        doc_url=row["doc_url"],
        heading_path=row["heading_path"],
        heading_title=row["heading_title"],
        content=row["content"],
        summary=row["summary"],
        content_preview=row["content_preview"],
        code_content=row["code_content"],
        token_count=row["token_count"],
        chunk_index=row["chunk_index"],
        trust_tier=row["trust_tier"],
        prev_chunk_id=row["prev_chunk_id"],
        next_chunk_id=row["next_chunk_id"],
    )
