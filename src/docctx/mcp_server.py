"""
MCP server entry point — exposes 3 tools via stdio transport.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from docctx.db.connection import init_db
from docctx.mcp.tools import handle_get_chunk, handle_list_packs, handle_search_docs

logger = logging.getLogger(__name__)

app = Server("docctx")

# ── Tool descriptions (guide agents toward efficient retrieval pattern) ────────

SEARCH_DOCS_DESCRIPTION = """\
Search documentation chunks using keyword/BM25 search.

RECOMMENDED PATTERN (most token-efficient):
1. list_packs → discover available packs
2. search_docs (response_mode="compact") → get chunk IDs and summaries (~50 tokens/chunk)
3. get_chunk (for relevant IDs) → get full content on demand

Token cost guide:
  compact:  ~50 tokens/chunk  (summary only)
  standard: ~115 tokens/chunk (summary + preview) [default]
  full:     ~250 tokens/chunk (full content)

Result is always a structured JSON object — never null.
Empty results include scanned counts and a suggestion.
"""

GET_CHUNK_DESCRIPTION = """\
Retrieve full content of a specific chunk by ID.
Use this after search_docs to lazily expand only the chunks you need.
Optionally include neighboring chunks (prev/next) and document metadata.
"""

LIST_PACKS_DESCRIPTION = """\
List all available documentation packs.
Use this before search_docs to know what documentation is available.
Returns: name, entry_url, version, chunk count, freshness status.
"""


# ── Tool schemas ──────────────────────────────────────────────────────────────


TOOLS = [
    types.Tool(
        name="search_docs",
        description=SEARCH_DOCS_DESCRIPTION,
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Supports camelCase and snake_case symbols.",
                },
                "pack": {
                    "type": "string",
                    "description": "Filter to specific pack(s). Supports glob patterns (e.g. 'react*').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max chunks to return (default 5, max 10).",
                    "minimum": 1,
                    "maximum": 10,
                },
                "min_confidence": {
                    "type": "string",
                    "enum": ["high", "low", "any"],
                    "description": "Minimum confidence level. 'high' = BM25 score ≥ 6.0, 'any' = above floor.",
                },
                "response_mode": {
                    "type": "string",
                    "enum": ["compact", "standard"],
                    "description": "Response detail level. 'compact' = summary only (~50 tok/chunk). 'standard' = summary + preview (~115 tok/chunk).",
                },
                "token_budget": {
                    "type": "integer",
                    "description": "Max tokens for response. System packs as many chunks as possible within budget.",
                },
            },
            "required": ["query"],
        },
    ),
    types.Tool(
        name="get_chunk",
        description=GET_CHUNK_DESCRIPTION,
        inputSchema={
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Chunk ID from search_docs result.",
                },
                "include_neighbors": {
                    "type": "boolean",
                    "description": "Include prev/next chunk summaries for context.",
                    "default": False,
                },
                "include_document_meta": {
                    "type": "boolean",
                    "description": "Include document metadata (title, fetched_at, URL).",
                    "default": False,
                },
                "token_budget": {
                    "type": "integer",
                    "description": "Max tokens for content. Content truncated if over budget.",
                },
            },
            "required": ["id"],
        },
    ),
    types.Tool(
        name="list_packs",
        description=LIST_PACKS_DESCRIPTION,
        inputSchema={
            "type": "object",
            "properties": {
                "name_pattern": {
                    "type": "string",
                    "description": "Optional glob filter for pack names (e.g. 'react*').",
                },
            },
        },
    ),
]


# ── Handler registration ──────────────────────────────────────────────────────


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    """Route tool calls to the appropriate handler."""
    arguments = arguments or {}

    if name == "search_docs":
        result = await handle_search_docs(arguments)
    elif name == "get_chunk":
        result = await handle_get_chunk(arguments)
    elif name == "list_packs":
        result = await handle_list_packs(arguments)
    else:
        import json
        result = json.dumps({
            "error": True,
            "error_code": "UNKNOWN_TOOL",
            "message": f"Unknown tool: {name}",
        })

    return [types.TextContent(type="text", text=result)]


# ── Entry point ───────────────────────────────────────────────────────────────


def serve() -> None:
    """Start the MCP server on stdio. Called by CLI `docctx serve`."""
    logging.basicConfig(
        level=logging.WARNING,  # Quiet for stdio MCP (logs would corrupt protocol)
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )

    # Initialize DB on startup
    try:
        init_db()
    except Exception as e:
        logger.error("Failed to initialize DB: %s", e)
        sys.exit(1)

    # Pre-warm service (bukan lazy init) supaya first query tidak kena cold start
    from docctx.mcp.tools import get_service
    try:
        svc = get_service()
        # Pre-warm read connection
        from docctx.db.connection import get_read_connection
        get_read_connection()
        logger.info("MCP server ready. Cache: %s", svc.cache_stats())
    except Exception as e:
        logger.warning("Service pre-warm failed: %s", e)

    async def main():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )

    asyncio.run(main())


if __name__ == "__main__":
    serve()
