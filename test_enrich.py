import asyncio
import os
from docctx.config import load_config
from docctx.models import Chunk
from docctx.ingestion.enricher import enrich_chunks

async def main():
    cfg = load_config()
    # Mock some chunks
    chunks = [
        Chunk(
            id="test1",
            pack_name="test_pack",
            doc_url="http://test.com",
            heading_path="Test > Path",
            heading_title="Path",
            content="This is the content of the first chunk to test the embedding.",
            summary="Test summary",
            content_preview="This is the content...",
            code_content="",
            token_count=10,
            chunk_index=0
        )
    ]
    
    # We will test local embeddings only, so disable LLM for this test
    cfg.ingestion.llm_summarize = False
    
    await enrich_chunks(chunks, cfg)
    
    for c in chunks:
        print(f"Chunk id: {c.id}")
        if c.embedding:
            print(f"Embedding length: {len(c.embedding)}")
            print(f"Embedding first 5: {c.embedding[:5]}")
        else:
            print("No embedding generated.")

if __name__ == "__main__":
    asyncio.run(main())
