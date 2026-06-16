"""
Integration test — full pipeline from HTML fixture to DB, then retrieval.
"""
import pytest
import tempfile
from pathlib import Path

from docctx.db.connection import db_connection, init_db
from docctx.db.queries import count_chunks, get_pack, list_packs
from docctx.ingestion.chunker import chunk_document
from docctx.ingestion.extractor import extract
from docctx.ingestion.indexer import finalize_pack_stats, index_document
from docctx.models import Document, Pack
from docctx.db.queries import insert_pack


SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head><title>React useEffect Hook</title></head>
<body>
<h1>useEffect Hook</h1>
<p>The useEffect hook lets you synchronize a component with an external system.</p>
<h2>Basic Usage</h2>
<p>Call useEffect at the top level of your component.</p>
<pre><code>useEffect(() => {
  // setup code
  return () => {
    // cleanup code
  };
}, [dependencies]);</code></pre>
<h2>Cleanup</h2>
<p>Return a cleanup function from useEffect to avoid memory leaks.</p>
<pre><code>useEffect(() => {
  const subscription = subscribe();
  return () => subscription.unsubscribe();
}, []);</code></pre>
</body>
</html>
"""


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Create a temporary database for testing."""
    db_path = tmp_path / "test.db"
    # Patch get_db_path to use temp DB
    import docctx.paths as paths
    monkeypatch.setattr(paths, "get_db_path", lambda: db_path)
    import docctx.db.connection as conn_module
    monkeypatch.setattr(conn_module, "get_db_path", lambda: db_path)
    init_db(db_path)
    return db_path


def test_full_pipeline_extract_chunk_index(temp_db):
    """Test: HTML → extract → chunk → index → count."""
    url = "https://react.dev/reference/react/useEffect"
    pack_name = "react-useeffect"

    # Extract
    extracted = extract(SAMPLE_HTML, url)
    assert extracted.title == "React useEffect Hook"
    assert len(extracted.markdown) > 50
    assert extracted.content_hash

    # Chunk
    chunks = chunk_document(
        markdown=extracted.markdown,
        pack_name=pack_name,
        doc_url=url,
    )
    assert len(chunks) >= 1

    # Index
    from docctx.db.connection import db_connection
    with db_connection(temp_db) as conn:
        pack = Pack(name=pack_name, entry_url=url, scope_rule="page-only")
        insert_pack(conn, pack)

        doc = Document(
            url=url,
            pack_name=pack_name,
            content_hash=extracted.content_hash,
            raw_markdown=extracted.markdown,
            title=extracted.title,
        )
        index_document(conn, doc, chunks)
        finalize_pack_stats(conn, pack_name)
        conn.commit()

        # Verify
        count = count_chunks(conn, pack_name)
        assert count == len(chunks)

        stored_pack = get_pack(conn, pack_name)
        assert stored_pack.chunk_count == len(chunks)


def test_retrieval_after_indexing(temp_db, monkeypatch):
    """Test: index content → search → find relevant chunks."""
    import docctx.paths as paths
    import docctx.db.connection as conn_module

    url = "https://react.dev/reference/react/useEffect"
    pack_name = "react-useeffect-retrieval"

    # Setup
    extracted = extract(SAMPLE_HTML, url)
    chunks = chunk_document(extracted.markdown, pack_name=pack_name, doc_url=url)

    with db_connection(temp_db) as conn:
        pack = Pack(name=pack_name, entry_url=url, scope_rule="page-only")
        insert_pack(conn, pack)
        doc = Document(
            url=url,
            pack_name=pack_name,
            content_hash=extracted.content_hash,
            raw_markdown=extracted.markdown,
            title=extracted.title,
        )
        index_document(conn, doc, chunks)
        finalize_pack_stats(conn, pack_name)
        conn.commit()

    # Search
    from docctx.retrieval.search import search_fts
    with db_connection(temp_db) as conn:
        results = search_fts(conn, "cleanup", top_k=5)

    # Should find chunks related to cleanup
    assert len(results) > 0
    # Top result should be relevant
    top_chunk, top_score = results[0]
    assert top_score > 0


def test_empty_search_returns_no_results(temp_db):
    """Test: searching empty DB returns empty list."""
    from docctx.retrieval.search import search_fts
    with db_connection(temp_db) as conn:
        results = search_fts(conn, "nonexistent query xyz", top_k=5)
    assert results == []
