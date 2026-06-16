"""
Inspect command — shows extraction result for a URL or pack structure.
"""
from __future__ import annotations

from typing import Optional

from docctx.db.connection import db_connection
from docctx.db.queries import list_documents, list_packs
from docctx.exceptions import PackNotFoundError


def inspect_url(url: str) -> dict:
    """
    Fetch and extract a URL, showing what docctx sees.
    Used for debugging extraction quality.
    """
    import asyncio
    import httpx
    from docctx.ingestion.extractor import extract
    from docctx.ingestion.chunker import chunk_document, estimate_tokens

    async def _fetch():
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            return resp.text

    html = asyncio.run(_fetch())
    extracted = extract(html, url)

    chunks = chunk_document(
        markdown=extracted.markdown,
        pack_name="_inspect",
        doc_url=url,
    )

    return {
        "url": url,
        "title": extracted.title,
        "content_hash": extracted.content_hash,
        "markdown_length": len(extracted.markdown),
        "token_estimate": estimate_tokens(extracted.markdown),
        "heading_count": len(extracted.heading_tree),
        "code_blocks": len(extracted.code_blocks),
        "chunks_produced": len(chunks),
        "headings": extracted.heading_tree[:20],  # first 20 headings
        "chunk_preview": [
            {
                "index": c.chunk_index,
                "heading_path": c.heading_path,
                "token_count": c.token_count,
                "summary": c.summary,
            }
            for c in chunks[:10]  # first 10 chunks
        ],
    }


def inspect_pack(pack_name: str) -> dict:
    """
    Show pack structure: heading tree, URL per node, chunk count per doc.
    """
    with db_connection() as conn:
        from docctx.db.queries import get_pack
        pack = get_pack(conn, pack_name)
        if pack is None:
            raise PackNotFoundError(
                f"Pack '{pack_name}' not found.",
                hint="Run `docctx list` to see available packs.",
            )

        docs = list_documents(conn, pack_name)

        # Count chunks per document
        doc_info = []
        for doc in docs:
            row = conn.execute(
                "SELECT COUNT(*) as n FROM chunks WHERE doc_url = ? AND pack_name = ?",
                (doc.url, pack_name),
            ).fetchone()
            chunk_count = row["n"] if row else 0

            # Get heading tree for this document from chunks
            heading_rows = conn.execute(
                "SELECT DISTINCT heading_path FROM chunks WHERE doc_url = ? AND pack_name = ? ORDER BY chunk_index",
                (doc.url, pack_name),
            ).fetchall()

            doc_info.append({
                "url": doc.url,
                "title": doc.title,
                "chunk_count": chunk_count,
                "fetch_status": doc.fetch_status,
                "heading_paths": [r["heading_path"] for r in heading_rows[:20]],
            })

    return {
        "pack_name": pack.name,
        "entry_url": pack.entry_url,
        "scope_rule": pack.scope_rule,
        "version": pack.version_tag,
        "trust_tier": pack.trust_tier,
        "total_docs": pack.doc_count,
        "total_chunks": pack.chunk_count,
        "freshness": pack.freshness,
        "last_refreshed": pack.last_refreshed.isoformat() if pack.last_refreshed else None,
        "documents": doc_info,
    }


def format_inspect_pack(data: dict) -> str:
    """Format inspect_pack output as human-readable text."""
    lines = [
        f"Pack: {data['pack_name']}",
        f"Entry URL: {data['entry_url']}",
        f"Scope: {data['scope_rule']} | Trust: {data['trust_tier']} | Freshness: {data['freshness']}",
        f"Documents: {data['total_docs']} | Chunks: {data['total_chunks']}",
        "",
    ]

    for doc in data["documents"]:
        status_icon = "✓" if doc["fetch_status"] == "ok" else "✗"
        lines.append(f"  {status_icon} {doc['url']} ({doc['chunk_count']} chunks)")
        if doc.get("title"):
            lines.append(f"      Title: {doc['title']}")
        for hp in doc.get("heading_paths", [])[:5]:
            lines.append(f"      → {hp}")
        if len(doc.get("heading_paths", [])) > 5:
            lines.append(f"      … and {len(doc['heading_paths']) - 5} more")

    return "\n".join(lines)
