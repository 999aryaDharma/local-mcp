"""
Explain command — runs retrieval pipeline in trace mode and formats human-readable output.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from docctx.config import load_config
from docctx.retrieval.service import RetrievalService


@dataclass
class ExplainReport:
    query: str
    pack_filter: Optional[str]
    scanned_packs: int
    scanned_chunks: int
    passed_count: int
    dropped_count: int
    floor_score: float
    confidence_cutoff: float
    results: list[dict]
    dropped: list[dict]


def explain_query(
    query: str,
    pack: Optional[str] = None,
    limit: int = 10,
) -> ExplainReport:
    """
    Run retrieval in trace mode, returning full ranking details.
    Shows all boosts applied, threshold decisions, and dropped chunks.
    """
    cfg = load_config()
    svc = RetrievalService(config=cfg)

    # Run with trace=True to capture full boost trace
    response = svc.search(
        query=query,
        pack=pack,
        limit=limit,
        response_mode="standard",
        trace=True,
    )

    results = []
    for i, chunk in enumerate(response.chunks, 1):
        results.append({
            "rank": i,
            "id": chunk.id,
            "pack": chunk.pack,
            "heading_path": chunk.heading_path,
            "score": chunk.score,
            "confidence": chunk.confidence,
            "boosts": chunk.boosts_applied,
            "summary": chunk.summary,
            "url": chunk.url,
        })

    return ExplainReport(
        query=query,
        pack_filter=pack,
        scanned_packs=response.scanned_packs,
        scanned_chunks=response.scanned_chunks,
        passed_count=len(response.chunks),
        dropped_count=len(response.dropped_chunks),
        floor_score=cfg.retrieval.floor_score,
        confidence_cutoff=cfg.retrieval.confidence_cutoff,
        results=results,
        dropped=response.dropped_chunks,
    )


def format_explain_report(report: ExplainReport) -> str:
    """Format an ExplainReport as a human-readable string."""
    lines = [
        f"Explain: '{report.query}'",
        f"Pack filter: {report.pack_filter or '(all)'}",
        f"Scanned: {report.scanned_packs} packs, {report.scanned_chunks} candidate chunks",
        f"Thresholds: floor={report.floor_score}, confidence_cutoff={report.confidence_cutoff}",
        "",
    ]

    if not report.results:
        lines.append("No results passed threshold.")
    else:
        lines.append(f"Results ({report.passed_count} passed threshold):")
        lines.append("-" * 60)
        for r in report.results:
            lines.append(
                f"[{r['rank']}] {r['heading_path']}"
            )
            lines.append(
                f"    Pack: {r['pack']}  Score: {r['score']:.3f}  Confidence: {r['confidence']}"
            )
            if r["boosts"]:
                boost_str = ", ".join(
                    f"{b['factor']}x{b['multiplier']}" for b in r["boosts"]
                )
                lines.append(f"    Boosts: {boost_str}")
            lines.append(f"    Summary: {r['summary']}")
            lines.append(f"    URL: {r['url']}")
            lines.append("")

    if report.dropped:
        lines.append(f"Dropped ({report.dropped_count} below threshold):")
        lines.append("-" * 60)
        for d in report.dropped[:10]: # show top 10 dropped
            lines.append(f"  - {d['heading_path']} (Score: {d['score']:.3f}, Conf: {d['confidence']})")
        if report.dropped_count > 10:
            lines.append(f"  ... and {report.dropped_count - 10} more")

    return "\n".join(lines)
