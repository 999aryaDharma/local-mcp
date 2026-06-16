"""
SQLite connection factory with required pragmas and custom functions.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from docctx.paths import get_db_path
from docctx.retrieval.tokenizer import normalize_for_index

SCHEMA_VERSION = 2


def normalize_text(text: str) -> str:
    """SQLite custom function — delegate ke tokenizer (single source of truth)."""
    return normalize_for_index(text)


def _setup_connection(conn: sqlite3.Connection) -> None:
    """Apply pragmas dan register custom functions. Dipanggil sekali per connection."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-32000")
    conn.create_function("normalize_text", 1, normalize_text)

    # Load sqlite-vec jika tersedia (M2)
    try:
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, sqlite3.OperationalError, AttributeError):
        pass


# ── Read connection pool ──────────────────────────────────────────────────────
# Satu read connection per thread, di-setup sekali, di-reuse terus.

_read_conn_lock = threading.Lock()
_read_conn: Optional[sqlite3.Connection] = None
_read_conn_db_path: Optional[Path] = None


def get_read_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """
    Return a persistent read-only connection.
    Setup (pragma + custom functions) hanya dijalankan sekali.
    """
    global _read_conn, _read_conn_db_path

    path = db_path or get_db_path()

    with _read_conn_lock:
        if _read_conn is not None and _read_conn_db_path != path:
            try:
                _read_conn.close()
            except Exception:
                pass
            _read_conn = None

        if _read_conn is None:
            conn = sqlite3.connect(
                str(path),
                check_same_thread=False,
            )
            _setup_connection(conn)
            _read_conn = conn
            _read_conn_db_path = path
        else:
            try:
                _read_conn.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                conn = sqlite3.connect(str(path), check_same_thread=False)
                _setup_connection(conn)
                _read_conn = conn

    return _read_conn


def invalidate_read_connection() -> None:
    """Dipanggil setelah write operations (add, refresh, remove)."""
    global _read_conn
    with _read_conn_lock:
        if _read_conn is not None:
            try:
                _read_conn.close()
            except Exception:
                pass
            _read_conn = None


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Buat fresh connection untuk write operations (ingestion, migration)."""
    path = db_path or get_db_path()
    conn = sqlite3.connect(str(path))
    _setup_connection(conn)
    return conn


@contextmanager
def db_connection(
    db_path: Optional[Path] = None,
    write: bool = False,
) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager untuk DB access.
    
    write=False (default): return persistent read connection.
    write=True: buat fresh connection, close setelah yield.
    """
    if write:
        conn = get_connection(db_path)
        try:
            yield conn
        finally:
            conn.close()
            invalidate_read_connection()
    else:
        yield get_read_connection(db_path)


def init_db(db_path: Optional[Path] = None) -> None:
    """Initialize schema. Selalu pakai fresh connection (write operation)."""
    schema_file = Path(__file__).parent / "schema.sql"
    schema_sql = schema_file.read_text(encoding="utf-8")

    with db_connection(db_path, write=True) as conn:
        conn.executescript(schema_sql)
        
        # Create vector table dynamically based on dimension
        from docctx.config import load_config
        cfg = load_config()
        dim = cfg.embeddings.dimension
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[{dim}])")
        
        conn.commit()


def get_schema_version(db_path: Optional[Path] = None) -> int:
    try:
        conn = get_read_connection(db_path)
        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return row["version"] if row else 0
    except sqlite3.OperationalError:
        return 0
