"""
Eval Harness for measuring retrieval performance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from docctx.config import load_config
from docctx.retrieval.service import RetrievalService


@dataclass
class EvalQuery:
    text: str
    category: str
    expected_chunk_id: Optional[str] = None
    expected_empty: bool = False


@dataclass
class QueryResult:
    query: str
    category: str
    hit_at_1: bool
    hit_at_5: bool
    is_empty: bool
    top_score: float
    expected_empty_matched: bool


@dataclass
class EvalReport:
    total_queries: int
    hit_at_1_rate: float
    hit_at_5_rate: float
    empty_rate: float
    avg_top_score: float
    results: list[QueryResult]

    @classmethod
    def from_results(cls, results: list[QueryResult]) -> EvalReport:
        if not results:
            return cls(0, 0.0, 0.0, 0.0, 0.0, [])

        total = len(results)
        
        # Calculate rates for queries that expect a hit
        hit_queries = [r for r in results if r.expected_empty_matched is False or not r.is_empty or r.hit_at_5] 
        hit_total = len(hit_queries)
        
        # This is a simplification. If we have expected chunks, use those.
        # If no expected chunk is provided, hit_at_1 is True if results are not empty
        
        hits_1 = sum(1 for r in hit_queries if r.hit_at_1)
        hits_5 = sum(1 for r in hit_queries if r.hit_at_5)
        
        empty_count = sum(1 for r in results if r.is_empty)
        top_scores = [r.top_score for r in results if r.top_score > 0]
        avg_score = sum(top_scores) / len(top_scores) if top_scores else 0.0

        return cls(
            total_queries=total,
            hit_at_1_rate=(hits_1 / hit_total) if hit_total else 0.0,
            hit_at_5_rate=(hits_5 / hit_total) if hit_total else 0.0,
            empty_rate=empty_count / total,
            avg_top_score=avg_score,
            results=results,
        )


class EvalDataset:
    def __init__(self, queries: list[EvalQuery]):
        self.queries = queries

    @classmethod
    def from_json(cls, path: str | Path) -> EvalDataset:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        queries = []
        for q in data.get("queries", []):
            queries.append(EvalQuery(
                text=q["text"],
                category=q.get("category", "general"),
                expected_chunk_id=q.get("expected_chunk_id"),
                expected_empty=q.get("expected_empty", False),
            ))
        return cls(queries=queries)


class EvalHarness:
    def __init__(self):
        self.svc = RetrievalService(config=load_config())

    def run(self, dataset: EvalDataset) -> EvalReport:
        results = []

        for query in dataset.queries:
            response = self.svc.search(query=query.text, limit=5)

            chunks = response.chunks
            is_empty = len(chunks) == 0
            
            hit_at_1 = False
            hit_at_5 = False
            
            if query.expected_chunk_id:
                if chunks and chunks[0].id == query.expected_chunk_id:
                    hit_at_1 = True
                if query.expected_chunk_id in [c.id for c in chunks]:
                    hit_at_5 = True
            elif not query.expected_empty and chunks:
                # If no expected chunk ID provided but we expect results, treat any hit as success for now
                # In real eval, we'd need exact IDs or semantic grading
                hit_at_1 = True
                hit_at_5 = True

            results.append(QueryResult(
                query=query.text,
                category=query.category,
                hit_at_1=hit_at_1,
                hit_at_5=hit_at_5,
                is_empty=is_empty,
                top_score=chunks[0].score if chunks else 0.0,
                expected_empty_matched=(is_empty == query.expected_empty),
            ))

        return EvalReport.from_results(results)
