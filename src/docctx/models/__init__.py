"""
Data models for docctx — Pack, Document, Chunk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from typing import Optional


class TrustTier(IntEnum):
    OFFICIAL = 1    # official documentation source
    COMMUNITY = 2   # community / third-party docs
    UNKNOWN = 3     # unclassified


class ScopeRule(StrEnum):
    PAGE_ONLY = "page-only"
    SIBLINGS = "siblings"
    SUBTREE = "subtree"
    SITE = "site"


class FetchStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Pack:
    name: str
    entry_url: str
    scope_rule: str
    trust_tier: int = TrustTier.OFFICIAL
    version_tag: Optional[str] = None
    last_refreshed: Optional[datetime] = None
    doc_count: int = 0
    chunk_count: int = 0
    created_at: Optional[datetime] = None

    @property
    def freshness(self) -> str:
        if self.last_refreshed is None:
            return "unknown"
        now = datetime.now(UTC)
        # Handle both timezone-aware and naive datetimes from DB
        lr = self.last_refreshed
        if lr.tzinfo is None:
            lr = lr.replace(tzinfo=UTC)
        delta = now - lr
        days = delta.days
        if days < 7:
            return "fresh"
        elif days < 30:
            return "stale"
        else:
            return "very_stale"


@dataclass
class Document:
    url: str
    pack_name: str
    content_hash: str
    raw_markdown: str
    title: Optional[str] = None
    fetch_status: str = FetchStatus.OK
    fetched_at: Optional[datetime] = None
    id: Optional[int] = None


@dataclass
class Chunk:
    pack_name: str
    doc_url: str
    heading_path: str          # e.g. "React > Hooks > useEffect"
    heading_title: str         # last heading in path
    content: str               # full chunk text
    summary: str               # rule-based summary (~120 chars)
    content_preview: str       # first 200 chars of content
    code_content: str          # extracted code blocks only
    token_count: int
    chunk_index: int           # position within document
    trust_tier: int = TrustTier.OFFICIAL
    prev_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None
    id: Optional[str] = None   # UUID assigned at index time
    llm_summary: Optional[str] = None # M2: Gemini generated summary
    embedding: Optional[list[float]] = None # M2: Vector representation
    extracted_relations: list[dict] = field(default_factory=list) # M2.3: Knowledge Graph metadata

    @classmethod
    def make_id(cls, pack_name: str, doc_url: str, chunk_index: int) -> str:
        """Generate a deterministic chunk ID."""
        import hashlib
        raw = f"{pack_name}::{doc_url}::{chunk_index}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
