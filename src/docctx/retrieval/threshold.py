"""
Threshold filtering — separates chunks into passed/dropped/below_floor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from docctx.retrieval.ranking import ScoredChunk


@dataclass
class ThresholdResult:
    passed: list[ScoredChunk]
    dropped: list[ScoredChunk]       # below floor, excluded from results
    max_score: float
    floor_score: float
    confidence_cutoff: float

    @property
    def result_status(self) -> str:
        if not self.passed:
            if self.dropped and any(d.final_score >= self.floor_score for d in self.dropped):
                return "low_confidence"  # Actually means filtered by min_confidence
            if self.max_score > 0:
                return "low_confidence"
            return "empty"
        return "ok"

    @property
    def suggestion(self) -> Optional[str]:
        if self.result_status == "empty":
            return (
                "No chunks found for this query. "
                "Try a different query, or check available packs with list_packs."
            )
        if self.result_status == "low_confidence":
            return (
                f"Best match scored {self.max_score:.1f} (floor={self.floor_score}). "
                "Try a more specific query or lower min_confidence."
            )
        return None


def apply_threshold(
    scored: list[ScoredChunk],
    floor_score: float = 3.0,
    confidence_cutoff: float = 6.0,
    min_confidence: str = "any",
) -> ThresholdResult:
    """
    Split scored chunks into passed (above floor) and dropped (below floor).

    min_confidence:
        'any'  → include all above floor
        'low'  → include all above floor (same as 'any')
        'high' → include only high confidence chunks
    """
    max_score = max((s.final_score for s in scored), default=0.0)
    passed = []
    dropped = []

    for s in scored:
        if s.final_score < floor_score:
            s.confidence = "below_floor"
            dropped.append(s)
        elif min_confidence == "high" and s.confidence != "high":
            dropped.append(s)
        else:
            passed.append(s)

    return ThresholdResult(
        passed=passed,
        dropped=dropped,
        max_score=max_score,
        floor_score=floor_score,
        confidence_cutoff=confidence_cutoff,
    )


def apply_token_budget(
    chunks: list[ScoredChunk],
    token_budget: int,
    mode: str = "standard",
) -> tuple[list[ScoredChunk], int]:
    """
    Trim chunk list to fit within a token budget.
    Returns (selected_chunks, tokens_used).

    Token estimates per mode:
    - compact: ~45 tokens/chunk (summary only)
    - standard: ~115 tokens/chunk (summary + preview)
    - full: ~250 tokens/chunk (full content)
    """
    TOKENS_PER_MODE = {
        "compact": 45,
        "standard": 115,
        "full": 250,
    }
    tokens_per_chunk = TOKENS_PER_MODE.get(mode, 115)
    envelope_tokens = 50  # JSON wrapper overhead

    budget_remaining = token_budget - envelope_tokens
    selected = []
    total_tokens = envelope_tokens

    for chunk in chunks:
        cost = tokens_per_chunk
        if total_tokens + cost <= token_budget:
            selected.append(chunk)
            total_tokens += cost
        else:
            break

    return selected, total_tokens
