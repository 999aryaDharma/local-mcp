"""
SQLite connection factory with required pragmas and custom functions.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from docctx.paths import get_db_path

SCHEMA_VERSION = 1


from docctx.retrieval.tokenizer import normalize_for_index

def normalize_text(text: str) -> str:
    """
    Pre-process text for FTS5 indexing.
    Delegates to normalize_for_index in tokenizer.py.
    """
    return normalize_for_index(text)


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """
    Create a new SQLite connection with all required pragmas and custom functions.
    Caller is responsible for closing the connection.
    """
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    
    # Load sqlite-vec extension
    conn.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    # Required pragmas
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")

    # Register custom normalize_text function for FTS5 triggers
    conn.create_function("normalize_text", 1, normalize_text)

    return conn


@contextmanager
def db_connection(db_path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager that yields a connection and closes it after use."""
    conn = get_connection(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Initialize the database schema if it doesn't exist."""
    schema_file = Path(__file__).parent / "schema.sql"
    schema_sql = schema_file.read_text(encoding="utf-8")

    with db_connection(db_path) as conn:
        # Execute schema (CREATE TABLE IF NOT EXISTS — safe to run multiple times)
        conn.executescript(schema_sql)
        
        # Create vector table dynamically based on dimension
        from docctx.config import load_config
        cfg = load_config()
        dim = cfg.embeddings.dimension
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])")
        
        conn.commit()


def get_schema_version(db_path: Path | None = None) -> int:
    """Return current schema version from DB, or 0 if uninitialized."""
    try:
        with db_connection(db_path) as conn:
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            return row["version"] if row else 0
    except sqlite3.OperationalError:
        return 0
