"""
Indexer — writes ingested data to SQLite in a single transaction.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from docctx.db.queries import (
    count_chunks,
    count_documents,
    delete_chunks_for_document,
    insert_chunk,
    insert_document,
    update_pack_stats,
)
from docctx.models import Chunk, Document

logger = logging.getLogger(__name__)


def index_document(
    conn: sqlite3.Connection,
    document: Document,
    chunks: list[Chunk],
    replace: bool = False,
) -> int:
    """
    Insert (or replace) a document and its chunks into the DB.
    Returns number of chunks indexed.
    """
    if replace:
        delete_chunks_for_document(conn, document.url, document.pack_name)

    insert_document(conn, document)

    for chunk in chunks:
        insert_chunk(conn, chunk)

    logger.debug(
        "Indexed %d chunks for %s in pack '%s'",
        len(chunks),
        document.url,
        document.pack_name,
    )
    return len(chunks)


def finalize_pack_stats(conn: sqlite3.Connection, pack_name: str) -> None:
    """Update pack.doc_count and pack.chunk_count after ingestion."""
    doc_count = count_documents(conn, pack_name)
    chunk_count = count_chunks(conn, pack_name)
    update_pack_stats(
        conn,
        pack_name,
        doc_count=doc_count,
        chunk_count=chunk_count,
        last_refreshed=datetime.now(UTC),
    )
