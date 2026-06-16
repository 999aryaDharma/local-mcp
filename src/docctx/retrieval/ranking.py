"""
Ranking — applies boost multipliers to FTS5 BM25 scores and produces BoostTrace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from docctx.config import RetrievalBoostsConfig
from docctx.models import Chunk


@dataclass
class BoostEntry:
    factor_name: str
    multiplier: float
    reason: str


@dataclass
class ScoredChunk:
    chunk: Chunk
    bm25_score: float
    final_score: float
    boosts: list[BoostEntry] = field(default_factory=list)
    confidence: str = "low"   # 'high' | 'low' | 'below_floor'

    @property
    def boost_multiplier(self) -> float:
        m = 1.0
        for b in self.boosts:
            m *= b.multiplier
        return m


def rank_chunks(
    candidates: list[tuple[Chunk, float]],   # (chunk, bm25_score)
    query: str,
    boosts_cfg: RetrievalBoostsConfig,
    floor_score: float = 3.0,
    confidence_cutoff: float = 6.0,
    pack_filter: Optional[str] = None,
    limit: int = 5,
    trace: bool = False,
) -> list[ScoredChunk]:
    """
    Apply boost multipliers to BM25 scores and filter/sort results.

    Boost stack:
    1. +1.5x if any query term in heading_path
    2. +1.3x if any query term in code_content
    3. +1.2x if trust_tier == 1 (official)
    4. +1.1x if heading_title exactly matches query
    """
    query_terms = query.lower().split()
    results: list[ScoredChunk] = []

    for chunk, bm25 in candidates:
        boosts: list[BoostEntry] = []

        # Boost 1: query term in heading_path
        heading_lower = chunk.heading_path.lower()
        if any(term in heading_lower for term in query_terms):
            boosts.append(
                BoostEntry(
                    factor_name="heading_exact",
                    multiplier=boosts_cfg.heading_exact,
                    reason=f"query term found in heading path: '{chunk.heading_path}'",
                )
            )

        # Boost 2: query term in code_content
        if chunk.code_content:
            code_lower = chunk.code_content.lower()
            if any(term in code_lower for term in query_terms):
                boosts.append(
                    BoostEntry(
                        factor_name="code_match",
                        multiplier=boosts_cfg.code_match,
                        reason="query term found in code block",
                    )
                )

        # Boost 3: official trust tier
        if chunk.trust_tier == 1:
            boosts.append(
                BoostEntry(
                    factor_name="trust_tier_official",
                    multiplier=boosts_cfg.trust_tier_official,
                    reason="source is official documentation",
                )
            )

        # Boost 4: heading_title exactly matches query
        if chunk.heading_title.lower() == query.lower():
            boosts.append(
                BoostEntry(
                    factor_name="heading_title_exact",
                    multiplier=boosts_cfg.heading_title_exact,
                    reason=f"heading title exactly matches query: '{chunk.heading_title}'",
                )
            )

        # Compute final score (multiplicative)
        boost_total = 1.0
        for b in boosts:
            boost_total *= b.multiplier
        final_score = bm25 * boost_total

        scored = ScoredChunk(
            chunk=chunk,
            bm25_score=bm25,
            final_score=final_score,
            boosts=boosts if trace else [],
        )

        results.append(scored)

    # Sort by final_score descending
    results.sort(key=lambda s: s.final_score, reverse=True)

    # Apply pack filter (glob)
    if pack_filter:
        import fnmatch
        results = [r for r in results if fnmatch.fnmatch(r.chunk.pack_name, pack_filter)]

    # Assign confidence labels
    for scored in results:
        if scored.final_score >= confidence_cutoff:
            scored.confidence = "high"
        elif scored.final_score >= floor_score:
            scored.confidence = "low"
        else:
            scored.confidence = "below_floor"

    return results[:limit] if not trace else results  # trace returns all for explain
