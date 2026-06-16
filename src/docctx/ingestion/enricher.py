import logging
import os
import asyncio
from typing import Optional

from docctx.config import DocctxConfig
from docctx.models import Chunk

logger = logging.getLogger(__name__)

# Global singletons for embeddings
_EMBEDDING_MODEL = None

def get_embedding_model(model_name: str):
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
            # Disable symlinks warning
            os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
            logger.info("Loading embedding model %s (this might take a while on first run)...", model_name)
            _EMBEDDING_MODEL = SentenceTransformer(model_name)
        except ImportError:
            logger.error("sentence-transformers not installed. Run `uv add sentence-transformers`")
            raise
    return _EMBEDDING_MODEL

async def enrich_chunks(chunks: list[Chunk], cfg: DocctxConfig) -> None:
    """Enrich chunks with LLM summaries and vector embeddings IN-PLACE."""
    
    # 1. LLM Summarize (if enabled)
    if cfg.ingestion.llm_summarize and cfg.ingestion.llm_provider == "gemini":
        api_key = os.environ.get(cfg.ingestion.api_key_env)
        if not api_key:
            logger.warning("Gemini API key not found in environment variable %s. Skipping LLM summaries.", cfg.ingestion.api_key_env)
        else:
            await _batch_llm_summarize(chunks, api_key, cfg.ingestion.llm_model)
            
    # 2. Embeddings (always active if hybrid mode is active, but we'll always compute them for M2 if dim > 0)
    if cfg.embeddings.provider == "local" and cfg.embeddings.model:
        model = get_embedding_model(cfg.embeddings.model)
        # Prepare text for embedding (using heading + content)
        texts = [f"{c.heading_path}\n{c.content}" for c in chunks]
        
        logger.debug("Generating embeddings for %d chunks...", len(chunks))
        # This is blocking, but should be fast enough for local CPU processing 
        # Alternatively we can run in a ThreadPool
        embeddings = model.encode(texts, convert_to_numpy=True)
        
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb.tolist()

async def _batch_llm_summarize(chunks: list[Chunk], api_key: str, model_name: str) -> None:
    """Call Gemini to summarize chunks. Done concurrently to save time."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.warning("google-genai not installed. Skipping LLM summaries.")
        return

    # We use asyncio.gather for concurrent requests.
    client = genai.Client(api_key=api_key)
    
    from pydantic import BaseModel, Field
    
    class KnowledgeExtraction(BaseModel):
        summary: str = Field(description="A single, highly dense sentence (max 150 chars) summarizing WHAT it does, HOW to use it, and key API details. No intro phrases.")
        dependencies: list[str] = Field(description="List of other specific packages, concepts, or functions mentioned as dependencies or related concepts.")
        warnings: list[str] = Field(description="Any deprecation or critical warnings mentioned.")

    async def summarize_chunk(chunk: Chunk):
        if not chunk.content.strip():
            return
            
        prompt = (
            f"You are a technical documentation summarizer for a coding agent.\n"
            f"Heading: {chunk.heading_path}\n"
            f"Content:\n{chunk.content}"
        )
        
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json",
                    response_schema=KnowledgeExtraction,
                )
            )
            if response.text:
                import json
                data = json.loads(response.text)
                if "summary" in data:
                    chunk.llm_summary = data["summary"].strip().replace('\n', ' ')
                
                # M2.3 Save relations
                relations = []
                for dep in data.get("dependencies", []):
                    relations.append({"type": "depends_on", "target": dep})
                for warn in data.get("warnings", []):
                    relations.append({"type": "warning", "target": warn})
                chunk.extracted_relations = relations
                
        except Exception as e:
            logger.debug("Gemini summarization failed for chunk %s: %s", chunk.id, e)
            
    # Process concurrently (with a small concurrency limit to avoid rate limits)
    # Since we might have many chunks, let's chunk them into batches of 10
    batch_size = 5
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        await asyncio.gather(*(summarize_chunk(c) for c in batch))
