"""
Ingestion pipeline — orchestrates the full add/refresh flow.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Optional

from docctx.config import DocctxConfig, load_config
from docctx.db.connection import db_connection, init_db
from docctx.db.queries import delete_pack, insert_pack, pack_exists
from docctx.exceptions import PackExistsError, PackNotFoundError
from docctx.ingestion.chunker import chunk_document
from docctx.ingestion.discovery import discover_urls
from docctx.ingestion.extractor import extract
from docctx.ingestion.fetcher import Fetcher
from docctx.ingestion.indexer import finalize_pack_stats, index_document
from docctx.ingestion.scope import resolve_scope
from docctx.models import Document, FetchStatus, Pack, TrustTier

logger = logging.getLogger(__name__)


@dataclass
class PageResult:
    url: str
    status: str          # 'ok' | 'failed' | 'unchanged' | 'skipped'
    chunks: int = 0
    error: Optional[str] = None


@dataclass
class IngestionResult:
    pack_name: str
    entry_url: str
    pages: list[PageResult] = field(default_factory=list)
    total_chunks: int = 0

    @property
    def ok_pages(self) -> list[PageResult]:
        return [p for p in self.pages if p.status == "ok"]

    @property
    def failed_pages(self) -> list[PageResult]:
        return [p for p in self.pages if p.status == "failed"]

    @property
    def unchanged_pages(self) -> list[PageResult]:
        return [p for p in self.pages if p.status == "unchanged"]


async def run_add(
    url: str,
    pack_name: Optional[str] = None,
    scope: Optional[str] = None,
    version: Optional[str] = None,
    trust_tier: int = TrustTier.OFFICIAL,
    config: Optional[DocctxConfig] = None,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> IngestionResult:
    """
    Add a new context pack. Raises PackExistsError if pack already exists.

    Args:
        url: Entry URL to ingest.
        pack_name: Name for the pack (default: derived from URL).
        scope: Crawl scope rule. Inferred if not provided.
        version: Optional version tag.
        trust_tier: Trust level (1=official, 2=community).
        config: Configuration (loaded from file if not provided).
        progress_cb: Optional callback(url, status) for progress reporting.
    """
    cfg = config or load_config()
    init_db()

    # Resolve scope (may raise ScopeAmbiguousError)
    scope_config = resolve_scope(url, scope)

    # Derive pack name from URL if not provided
    if not pack_name:
        pack_name = _derive_pack_name(url)

    # Check for existing pack
    with db_connection() as conn:
        if pack_exists(conn, pack_name):
            raise PackExistsError(
                f"Pack '{pack_name}' already exists. Use `docctx refresh {pack_name}` to update.",
                hint=f"Run: docctx refresh {pack_name}",
            )

    # Create pack record
    pack = Pack(
        name=pack_name,
        entry_url=url,
        scope_rule=scope_config.rule,
        trust_tier=trust_tier,
        version_tag=version,
    )

    with db_connection() as conn:
        insert_pack(conn, pack)
        conn.commit()

    return await _run_ingestion(
        pack_name=pack_name,
        entry_url=url,
        scope_config=scope_config,
        trust_tier=trust_tier,
        cfg=cfg,
        replace=False,
        progress_cb=progress_cb,
    )


async def run_refresh(
    pack_name: str,
    force: bool = False,
    config: Optional[DocctxConfig] = None,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> IngestionResult:
    """
    Re-crawl an existing pack. Raises PackNotFoundError if pack doesn't exist.
    Incremental by default (skips unchanged pages). Use force=True for full re-ingest.
    """
    cfg = config or load_config()

    with db_connection() as conn:
        from docctx.db.queries import get_pack
        pack = get_pack(conn, pack_name)

    if pack is None:
        raise PackNotFoundError(
            f"Pack '{pack_name}' does not exist.",
            hint=f"Run `docctx list` to see available packs.",
        )

    scope_config = resolve_scope(pack.entry_url, pack.scope_rule)

    # Invalidate cache if force refresh
    if force:
        from docctx.paths import get_cache_dir
        import hashlib
        cache_dir = get_cache_dir()
        logger.info("Force refresh: clearing cache for pack '%s'", pack_name)

    return await _run_ingestion(
        pack_name=pack_name,
        entry_url=pack.entry_url,
        scope_config=scope_config,
        trust_tier=pack.trust_tier,
        cfg=cfg,
        replace=True,
        force_cache=force,
        progress_cb=progress_cb,
    )


async def _run_ingestion(
    pack_name: str,
    entry_url: str,
    scope_config,
    trust_tier: int,
    cfg: DocctxConfig,
    replace: bool,
    force_cache: bool = False,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> IngestionResult:
    """Core ingestion loop: discover → fetch → extract → chunk → index."""
    result = IngestionResult(pack_name=pack_name, entry_url=entry_url)

    async with Fetcher(
        rate_limit_rps=cfg.ingestion.rate_limit_rps,
        timeout=cfg.ingestion.request_timeout_sec,
        user_agent=cfg.ingestion.user_agent,
        cache_enabled=cfg.storage.cache_enabled,
        respect_robots=cfg.ingestion.respect_robots,
    ) as fetcher:
        # Discover URLs
        if progress_cb:
            progress_cb(entry_url, "discovering")

        urls = await discover_urls(
            scope_config,
            fetcher.client,
            max_pages=cfg.ingestion.max_pages,
            max_depth=cfg.ingestion.max_depth,
        )

        # Ensure entry_url is always first
        if entry_url not in urls:
            urls.insert(0, entry_url)

        logger.info("Ingesting %d URLs for pack '%s'", len(urls), pack_name)

        # Process each URL
        with db_connection() as conn:
            for url in urls:
                page_result = await _process_url(
                    url=url,
                    pack_name=pack_name,
                    trust_tier=trust_tier,
                    fetcher=fetcher,
                    conn=conn,
                    cfg=cfg,
                    replace=replace,
                    force_cache=force_cache,
                    progress_cb=progress_cb,
                )
                result.pages.append(page_result)
                result.total_chunks += page_result.chunks

            # Update pack stats
            finalize_pack_stats(conn, pack_name)
            conn.commit()

    return result


async def _process_url(
    url: str,
    pack_name: str,
    trust_tier: int,
    fetcher: Fetcher,
    conn,
    cfg: DocctxConfig,
    replace: bool,
    force_cache: bool,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> PageResult:
    """Fetch, extract, chunk and index a single URL."""
    if progress_cb:
        progress_cb(url, "fetching")

    # Invalidate cache if force refresh
    if force_cache:
        fetcher.invalidate_cache(url)

    try:
        from docctx.db.queries import get_document

        html = await fetcher.fetch(url)

        # Extract content
        from docctx.ingestion.extractor import extract as extract_html
        extracted = extract_html(html, url)

        # Check if content changed (incremental)
        if not replace and not force_cache:
            existing = get_document(conn, url, pack_name)
            if existing and existing.content_hash == extracted.content_hash:
                return PageResult(url=url, status="unchanged", chunks=0)

        # Build Document model
        document = Document(
            url=url,
            pack_name=pack_name,
            content_hash=extracted.content_hash,
            raw_markdown=extracted.markdown,
            title=extracted.title,
            fetch_status=FetchStatus.OK,
            fetched_at=datetime.now(UTC),
        )

        # Chunk
        if progress_cb:
            progress_cb(url, "chunking")

        chunks = chunk_document(
            markdown=extracted.markdown,
            pack_name=pack_name,
            doc_url=url,
            trust_tier=trust_tier,
            max_tokens=cfg.chunking.max_tokens,
            min_tokens=cfg.chunking.min_tokens,
            target_tokens=cfg.chunking.target_tokens,
        )

        # Index (single transaction batch)
        if progress_cb:
            progress_cb(url, "indexing")

        n_chunks = index_document(conn, document, chunks, replace=replace)
        conn.commit()
        return PageResult(url=url, status="ok", chunks=n_chunks)

    except Exception as e:
        logger.warning("Failed to process %s: %s", url, e)
        # Record failed document
        failed_doc = Document(
            url=url,
            pack_name=pack_name,
            content_hash="",
            raw_markdown="",
            title=None,
            fetch_status=FetchStatus.FAILED,
            fetched_at=datetime.now(UTC),
        )
        try:
            from docctx.db.queries import insert_document
            insert_document(conn, failed_doc)
            conn.commit()
        except Exception:
            pass
        return PageResult(url=url, status="failed", chunks=0, error=str(e))


def _derive_pack_name(url: str) -> str:
    """Derive a pack name from a URL (e.g. 'react-useeffect' from URL)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    # Use netloc + path slugified
    parts = parsed.netloc.split(".")
    # Remove 'www', 'docs', 'developer' prefix noise
    meaningful = [p for p in parts if p not in ("www", "com", "org", "io", "net")]
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]

    name_parts = meaningful[:1] + path_parts[:2]
    if not name_parts:
        name_parts = [parsed.netloc]

    slug = "-".join(name_parts)
    # Sanitize: only alphanumeric, hyphens, underscores
    import re
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:50] or "pack"
