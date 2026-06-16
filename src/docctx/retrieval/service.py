"""
RetrievalService — orchestrates FTS5 → Boost → Threshold → Format pipeline.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from docctx.config import DocctxConfig, load_config
from docctx.db.connection import db_connection
from docctx.db.queries import get_chunk_by_id, list_packs
from docctx.models import Chunk, Pack
from docctx.retrieval.ranking import ScoredChunk, rank_chunks
from docctx.retrieval.search import search_fts
from docctx.retrieval.threshold import ThresholdResult, apply_threshold, apply_token_budget

logger = logging.getLogger(__name__)


@dataclass
class ChunkResult:
    id: str
    pack: str
    heading_path: str
    summary: str
    content_preview: str
    url: str
    score: float
    confidence: str
    boosts_applied: list[dict] = field(default_factory=list)
    content: Optional[str] = None     # only in full mode


@dataclass
class SearchResponse:
    chunks: list[ChunkResult]
    result_status: str                 # 'ok' | 'empty' | 'low_confidence' | 'error'
    scanned_packs: int
    scanned_chunks: int
    max_score: float
    suggestion: Optional[str] = None
    token_usage: Optional[int] = None
    dropped_chunks: list[dict] = field(default_factory=list)


@dataclass
class GetChunkResponse:
    chunk: dict
    previous: Optional[dict] = None
    next: Optional[dict] = None
    document_meta: Optional[dict] = None
    relations: list[dict] = field(default_factory=list)


class RetrievalService:
    """
    Core retrieval service. Coordinates FTS5 search, boost ranking,
    threshold filtering, and response formatting.

    Operates in offline/read-only mode — no network calls.
    """

    def __init__(self, config: Optional[DocctxConfig] = None):
        self.cfg = config or load_config()
        from docctx.retrieval.cache import QueryCache
        # Using default cache settings as fallback since we might not have added them to config.py yet
        cache_max_size = getattr(self.cfg.retrieval, "cache_max_size", 100)
        cache_ttl_seconds = getattr(self.cfg.retrieval, "cache_ttl_seconds", 300.0)
        self._cache = QueryCache(max_size=cache_max_size, ttl_seconds=cache_ttl_seconds)

    def search(
        self,
        query: str,
        pack: Optional[str] = None,
        limit: Optional[int] = None,
        min_confidence: str = "any",
        response_mode: str = "standard",
        token_budget: Optional[int] = None,
        trace: bool = False,
    ) -> SearchResponse:
        """
        Full retrieval pipeline:
        FTS5 → Boost → Filter → Threshold → Token Budget → Format
        """
        cfg = self.cfg
        effective_limit = min(limit or cfg.retrieval.default_limit, cfg.retrieval.max_limit)

        # Cache lookup
        if not trace and token_budget is None:
            cached = self._cache.get(query, pack, effective_limit, min_confidence)
            if cached is not None:
                return cached

        with db_connection() as conn:
            mode = cfg.retrieval.mode
            candidates: list[tuple[Chunk, float]] = []
            fts_candidates: list[tuple[Chunk, float]] = []
            vec_candidates: list[tuple[Chunk, float]] = []

            # 1. FTS Search
            if mode in ("keyword", "hybrid"):
                fts_candidates = search_fts(conn, query, pack_filter=pack, top_k=20)
                if mode == "keyword":
                    candidates = fts_candidates
            
            # 2. Vector Search
            if mode in ("semantic", "hybrid"):
                from docctx.ingestion.enricher import get_embedding_model
                from docctx.retrieval.search import search_vector
                try:
                    model = get_embedding_model(cfg.embeddings.model)
                    # encode returns a list/array, we need a flat list of floats
                    query_embedding = model.encode([query], convert_to_numpy=True)[0].tolist()
                    vec_candidates = search_vector(conn, query_embedding, pack_filter=pack, top_k=20)
                    if mode == "semantic":
                        # Invert distance to score so rank_chunks doesn't break
                        # Distance is usually 0.0 to 2.0. Let's make score = 2.0 - dist
                        candidates = [(c, max(0.1, 2.0 - score) * 5) for c, score in vec_candidates]
                except Exception as e:
                    logger.warning("Semantic search failed: %s", e)
                    if mode == "semantic":
                        candidates = []

            # 3. Hybrid RRF
            if mode == "hybrid":
                # Combine using RRF
                k = cfg.retrieval.rrf_k
                scores: dict[str, float] = {}
                chunk_map: dict[str, Chunk] = {}
                
                # list 1
                for rank, (chunk, _) in enumerate(fts_candidates):
                    scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)
                    chunk_map[chunk.id] = chunk
                    
                # list 2
                for rank, (chunk, _) in enumerate(vec_candidates):
                    scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)
                    chunk_map[chunk.id] = chunk
                    
                # Scale RRF scores so they work with rank_chunks thresholds (max score ~10.0)
                scale_factor = k * 5
                candidates = [(chunk_map[cid], score * scale_factor) for cid, score in scores.items()]
                candidates.sort(key=lambda x: x[1], reverse=True)

            scanned_chunks = len(candidates)
            scanned_packs = len(set(chunk.pack_name for chunk, _ in candidates))

            if not candidates:
                # Check if there are any packs at all
                any_packs = conn.execute("SELECT 1 FROM packs LIMIT 1").fetchone()
                if any_packs is None:
                    response = SearchResponse(
                        chunks=[],
                        result_status="empty",
                        scanned_packs=0,
                        scanned_chunks=0,
                        max_score=0.0,
                        suggestion="No packs found. Add documentation with `docctx add <url>`.",
                    )
                    if not trace: self._cache.set(query, pack, effective_limit, min_confidence, response)
                    return response

            if not candidates:
                response = SearchResponse(
                    chunks=[],
                    result_status="empty",
                    scanned_packs=scanned_packs,
                    scanned_chunks=0,
                    max_score=0.0,
                    suggestion=(
                        "No chunks matched this query. "
                        "Try different terms or check `docctx list` for available packs."
                    ),
                )
                if not trace: self._cache.set(query, pack, effective_limit, min_confidence, response)
                return response

            # Boost re-rank
            scored = rank_chunks(
                candidates=candidates,
                query=query,
                boosts_cfg=cfg.retrieval.boosts,
                floor_score=cfg.retrieval.floor_score,
                confidence_cutoff=cfg.retrieval.confidence_cutoff,
                limit=effective_limit * 2,  # over-fetch before threshold
                trace=trace,
            )

            # Threshold filter
            threshold_result = apply_threshold(
                scored=scored,
                floor_score=cfg.retrieval.floor_score,
                confidence_cutoff=cfg.retrieval.confidence_cutoff,
                min_confidence=min_confidence,
            )

            # Apply limit
            final_chunks = threshold_result.passed[:effective_limit]

            # Apply token budget if specified
            actual_token_usage = None
            if token_budget is not None:
                final_chunks, actual_token_usage = apply_token_budget(
                    final_chunks, token_budget, response_mode
                )

            # Format response
            chunk_results = [
                self._format_chunk(sc, response_mode) for sc in final_chunks
            ]

            # Collect dropped chunks if tracing
            dropped_info = []
            if trace:
                for dc in threshold_result.dropped:
                    dropped_info.append({
                        "id": dc.chunk.id,
                        "heading_path": dc.chunk.heading_path,
                        "score": dc.final_score,
                        "confidence": getattr(dc, "confidence", "below_floor")
                    })

            response = SearchResponse(
                chunks=chunk_results,
                result_status=threshold_result.result_status,
                scanned_packs=scanned_packs,
                scanned_chunks=scanned_chunks,
                max_score=threshold_result.max_score,
                suggestion=threshold_result.suggestion,
                token_usage=actual_token_usage,
                dropped_chunks=dropped_info,
            )

            if not trace and response.result_status != "error":
                self._cache.set(query, pack, effective_limit, min_confidence, response)
            
            return response

    def invalidate_cache(self, pack_name: Optional[str] = None) -> None:
        self._cache.invalidate(pack_name)

    def cache_stats(self) -> dict:
        return self._cache.stats()

    def get_chunk(
        self,
        chunk_id: str,
        include_neighbors: bool = False,
        include_document_meta: bool = False,
        token_budget: Optional[int] = None,
    ) -> Optional[GetChunkResponse]:
        """Retrieve full chunk content by ID, optionally with neighbors."""
        with db_connection() as conn:
            chunk = get_chunk_by_id(conn, chunk_id)
            if chunk is None:
                return None

            chunk_dict = self._chunk_to_full_dict(chunk)

            # Budget check
            if token_budget is not None:
                from docctx.ingestion.chunker import estimate_tokens
                if estimate_tokens(chunk.content) > token_budget:
                    # Truncate content to fit budget
                    max_chars = token_budget * 4
                    chunk_dict["content"] = chunk.content[:max_chars] + "…[truncated]"
                    chunk_dict["truncated"] = True

            prev_dict = None
            next_dict = None

            if include_neighbors:
                if chunk.prev_chunk_id:
                    prev = get_chunk_by_id(conn, chunk.prev_chunk_id)
                    if prev:
                        prev_dict = {
                            "id": prev.id,
                            "heading_path": prev.heading_path,
                            "summary": prev.summary,
                        }
                if chunk.next_chunk_id:
                    nxt = get_chunk_by_id(conn, chunk.next_chunk_id)
                    if nxt:
                        next_dict = {
                            "id": nxt.id,
                            "heading_path": nxt.heading_path,
                            "summary": nxt.summary,
                        }

            doc_meta = None
            if include_document_meta:
                from docctx.db.queries import get_document
                doc = get_document(conn, chunk.doc_url, chunk.pack_name)
                if doc:
                    doc_meta = {
                        "url": doc.url,
                        "title": doc.title,
                        "fetched_at": doc.fetched_at.isoformat() if doc.fetched_at else None,
                        "fetch_status": doc.fetch_status,
                    }

            # Fetch M2.3 relations
            edges = conn.execute(
                "SELECT target_concept, relation_type FROM concept_edges WHERE chunk_id = ?",
                (chunk.id,)
            ).fetchall()
            relations = [{"type": row["relation_type"], "target": row["target_concept"]} for row in edges]

            return GetChunkResponse(
                chunk=chunk_dict,
                previous=prev_dict,
                next=next_dict,
                document_meta=doc_meta,
                relations=relations,
            )

    def list_packs(self, name_pattern: Optional[str] = None) -> list[dict]:
        """List all packs with freshness info."""
        with db_connection() as conn:
            packs = list_packs(conn, name_pattern)
        return [self._pack_to_dict(p) for p in packs]

    def _format_chunk(self, sc: ScoredChunk, mode: str) -> ChunkResult:
        chunk = sc.chunk
        result = ChunkResult(
            id=chunk.id or "",
            pack=chunk.pack_name,
            heading_path=chunk.heading_path,
            summary=chunk.llm_summary if chunk.llm_summary else chunk.summary,
            content_preview=chunk.content_preview,
            url=chunk.doc_url,
            score=round(sc.final_score, 3),
            confidence=sc.confidence,
            boosts_applied=[
                {"factor": b.factor_name, "multiplier": b.multiplier, "reason": b.reason}
                for b in sc.boosts
            ] if sc.boosts else [],
        )
        # Full mode: include full content directly
        if mode == "full":
            result.content = chunk.content
        return result

    def _chunk_to_full_dict(self, chunk: Chunk) -> dict:
        return {
            "id": chunk.id,
            "pack": chunk.pack_name,
            "heading_path": chunk.heading_path,
            "heading_title": chunk.heading_title,
            "summary": chunk.llm_summary if chunk.llm_summary else chunk.summary,
            "content": chunk.content,
            "code_content": chunk.code_content or None,
            "url": chunk.doc_url,
            "token_count": chunk.token_count,
            "trust_tier": chunk.trust_tier,
        }

    def _pack_to_dict(self, pack: Pack) -> dict:
        return {
            "name": pack.name,
            "entry_url": pack.entry_url,
            "version": pack.version_tag,
            "chunks": pack.chunk_count,
            "docs": pack.doc_count,
            "last_refreshed": pack.last_refreshed.isoformat() if pack.last_refreshed else None,
            "freshness": pack.freshness,
            "scope_rule": pack.scope_rule,
            "trust_tier": pack.trust_tier,
        }
