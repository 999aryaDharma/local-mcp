import asyncio
import os
from docctx.config import load_config
from docctx.db.connection import init_db
from docctx.ingestion.pipeline import run_add
from docctx.retrieval.service import RetrievalService

async def main():
    cfg = load_config()
    cfg.retrieval.mode = "hybrid"
    cfg.ingestion.llm_summarize = False # disable LLM summary for testing speed
    
    # Initialize DB (it creates chunks_vec etc.)
    init_db(dimension=cfg.embeddings.dimension) if 'dimension' in init_db.__code__.co_varnames else init_db()

    print("Indexing document...")
    # Add a mock file using local file URI
    try:
        await run_add("https://example.com/testdoc", pack_name="test_hybrid", config=cfg)
    except Exception as e:
        print(f"Assuming already indexed or failed: {e}")

    print("Searching...")
    svc = RetrievalService(cfg)
    resp = svc.search("testdoc", pack="test_hybrid", trace=True)
    
    print(f"Status: {resp.result_status}")
    print(f"Chunks returned: {len(resp.chunks)}")
    for c in resp.chunks:
        print(f" - {c.score}: {c.summary}")
    
if __name__ == "__main__":
    asyncio.run(main())
