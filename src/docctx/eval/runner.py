import json
import logging
from typing import Optional
from dataclasses import dataclass
from pathlib import Path

from docctx.config import load_config
from docctx.retrieval.service import RetrievalService

logger = logging.getLogger(__name__)

@dataclass
class EvalResult:
    total_queries: int
    hit_at_1: float
    hit_at_5: float
    avg_tokens: float
    empty_rate: float

def run_evaluation(dataset_path: str, pack_name: Optional[str] = None) -> EvalResult:
    """Run evaluation harness against a JSON dataset."""
    cfg = load_config()
    service = RetrievalService(cfg)
    
    path = Path(dataset_path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")
        
    with open(path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
        
    total = 0
    hits_1 = 0
    hits_5 = 0
    tokens_acc = 0
    empty_count = 0
    
    for item in dataset:
        query = item.get("query")
        expected_chunk = item.get("expected_chunk_id")
        should_be_empty = item.get("should_be_empty", False)
        
        if not query:
            continue
            
        total += 1
        resp = service.search(query, pack=pack_name, limit=5, trace=True)
        
        if resp.result_status == "empty" or len(resp.chunks) == 0:
            if should_be_empty:
                empty_count += 1
            continue
            
        # Count tokens
        for c in resp.chunks:
            tokens_acc += len(c.summary.split()) # Rough estimate
            
        if expected_chunk:
            chunk_ids = [c.id for c in resp.chunks]
            if chunk_ids and chunk_ids[0] == expected_chunk:
                hits_1 += 1
            if expected_chunk in chunk_ids:
                hits_5 += 1
                
    result = EvalResult(
        total_queries=total,
        hit_at_1=(hits_1 / total * 100) if total > 0 else 0.0,
        hit_at_5=(hits_5 / total * 100) if total > 0 else 0.0,
        avg_tokens=(tokens_acc / total) if total > 0 else 0.0,
        empty_rate=(empty_count / total * 100) if total > 0 else 0.0,
    )
    
    return result
