import os
import glob
from pathlib import Path
from typing import Optional, Callable
import logging

from docctx.config import DocctxConfig
from docctx.models import Document, FetchStatus, TrustTier
from docctx.ingestion.chunker import chunk_document

logger = logging.getLogger(__name__)

async def ingest_local(
    path: str,
    pack_name: str,
    conn,
    cfg: DocctxConfig,
    trust_tier: int = TrustTier.OFFICIAL,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> tuple[int, int, list[Document]]:
    """Ingest local markdown files."""
    
    base_dir = Path(path).resolve()
    if not base_dir.exists():
        raise FileNotFoundError(f"Directory not found: {base_dir}")

    total_chunks = 0
    total_docs = 0
    failed_docs = []
    
    from docctx.ingestion.indexer import index_document
    from docctx.db.queries import get_document
    from docctx.ingestion.enricher import enrich_chunks
    
    # Find markdown files
    md_files = []
    for ext in ("*.md", "*.mdx", "*.txt", "*.rst"):
        md_files.extend(base_dir.rglob(ext))
        
    for file_path in md_files:
        url = f"file://{file_path.as_posix()}"
        if progress_cb:
            progress_cb(url, "reading")
            
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
            
        import hashlib
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        
        # Check cache
        existing = get_document(conn, url, pack_name)
        if existing and existing.content_hash == content_hash:
            continue
            
        doc = Document(
            url=url,
            pack_name=pack_name,
            content_hash=content_hash,
            raw_markdown=content,
            title=file_path.name,
            fetch_status=FetchStatus.OK,
        )
        
        if progress_cb:
            progress_cb(url, "chunking")
            
        chunks = chunk_document(
            markdown=content,
            pack_name=pack_name,
            doc_url=url,
            trust_tier=trust_tier,
            max_tokens=cfg.chunking.max_tokens,
            min_tokens=cfg.chunking.min_tokens,
            target_tokens=cfg.chunking.target_tokens,
        )
        
        if chunks:
            if progress_cb:
                progress_cb(url, "enriching")
            await enrich_chunks(chunks, cfg)
            
            if progress_cb:
                progress_cb(url, "indexing")
            n = index_document(conn, doc, chunks, replace=True)
            total_chunks += n
            total_docs += 1
            
    conn.commit()
    return total_docs, total_chunks, failed_docs

async def ingest_git(
    repo_url: str,
    pack_name: str,
    conn,
    cfg: DocctxConfig,
    trust_tier: int = TrustTier.OFFICIAL,
    progress_cb: Optional[Callable[[str, str], None]] = None,
) -> tuple[int, int, list[Document]]:
    """Clone git repo shallowly and ingest it."""
    import tempfile
    import subprocess
    import shutil
    
    if progress_cb:
        progress_cb(repo_url, "cloning")
        
    temp_dir = tempfile.mkdtemp(prefix="docctx_git_")
    try:
        # Shallow clone
        cmd = ["git", "clone", "--depth", "1", repo_url, temp_dir]
        subprocess.run(cmd, check=True, capture_output=True)
        
        # Look for docs/ folder, or just use the root if not found
        docs_dir = Path(temp_dir) / "docs"
        target_dir = docs_dir if docs_dir.exists() and docs_dir.is_dir() else Path(temp_dir)
        
        return await ingest_local(
            path=str(target_dir),
            pack_name=pack_name,
            conn=conn,
            cfg=cfg,
            trust_tier=trust_tier,
            progress_cb=progress_cb
        )
    except subprocess.CalledProcessError as e:
        logger.error("Git clone failed: %s", e.stderr.decode())
        raise RuntimeError(f"Git clone failed: {e.stderr.decode()}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
