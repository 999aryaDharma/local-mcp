"""
MCP tool handlers — thin wrappers over RetrievalService.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from docctx.retrieval.service import RetrievalService

logger = logging.getLogger(__name__)

_svc: RetrievalService | None = None


def get_service() -> RetrievalService:
    global _svc
    if _svc is None:
        _svc = RetrievalService()
    return _svc


def _omit_none(d: dict) -> dict:
    """Recursively remove None values and empty lists from a dict."""
    result = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, list) and len(v) == 0:
            continue
        if isinstance(v, dict):
            v = _omit_none(v)
        result[k] = v
    return result


async def handle_search_docs(arguments: dict[str, Any]) -> str:
    """
    search_docs MCP tool handler.
    Returns structured JSON response. Never returns null.
    """
    query = arguments.get("query", "")
    pack = arguments.get("pack")
    limit = arguments.get("limit")
    min_confidence = arguments.get("min_confidence", "any")
    response_mode = arguments.get("response_mode", "standard")
    token_budget = arguments.get("token_budget")

    if not query:
        return json.dumps({
            "error": True,
            "error_code": "INVALID_INPUT",
            "message": "query is required",
            "chunks": [],
            "result_status": "error",
        })

    try:
        svc = get_service()
        response = svc.search(
            query=query,
            pack=pack,
            limit=limit,
            min_confidence=min_confidence,
            response_mode=response_mode,
            token_budget=token_budget,
        )

        result = {
            "chunks": [
                _omit_none({
                    "id": c.id,
                    "pack": c.pack,
                    "heading_path": c.heading_path,
                    "summary": c.summary,
                    "content_preview": c.content_preview,
                    "content": c.content,
                    "url": c.url,
                    "score": c.score,
                    "confidence": c.confidence,
                    "boosts_applied": c.boosts_applied,
                })
                for c in response.chunks
            ],
            "result_status": response.result_status,
            "scanned_packs": response.scanned_packs,
            "scanned_chunks": response.scanned_chunks,
            "max_score": round(response.max_score, 3),
        }

        if response.suggestion:
            result["suggestion"] = response.suggestion
        if response.token_usage is not None:
            result["token_usage"] = response.token_usage

        return json.dumps(result)

    except Exception as e:
        logger.exception("search_docs error")
        return json.dumps({
            "error": True,
            "error_code": "INTERNAL_ERROR",
            "message": str(e),
            "chunks": [],
            "result_status": "error",
        })


async def handle_get_chunk(arguments: dict[str, Any]) -> str:
    """
    get_chunk MCP tool handler.
    Returns full chunk content + optional neighbors and document metadata.
    """
    chunk_id = arguments.get("id", "")
    include_neighbors = arguments.get("include_neighbors", False)
    include_document_meta = arguments.get("include_document_meta", False)
    token_budget = arguments.get("token_budget")

    if not chunk_id:
        return json.dumps({
            "error": True,
            "error_code": "INVALID_INPUT",
            "message": "id is required",
        })

    try:
        svc = get_service()
        response = svc.get_chunk(
            chunk_id=chunk_id,
            include_neighbors=include_neighbors,
            include_document_meta=include_document_meta,
            token_budget=token_budget,
        )

        if response is None:
            return json.dumps({
                "error": True,
                "error_code": "NOT_FOUND",
                "message": f"Chunk '{chunk_id}' not found",
            })

        result = _omit_none({
            "chunk": _omit_none(response.chunk),
            "previous": response.previous,
            "next": response.next,
            "document_meta": response.document_meta,
        })
        return json.dumps(result)

    except Exception as e:
        logger.exception("get_chunk error")
        return json.dumps({
            "error": True,
            "error_code": "INTERNAL_ERROR",
            "message": str(e),
        })


async def handle_list_packs(arguments: dict[str, Any]) -> str:
    """
    list_packs MCP tool handler.
    Returns array of packs with freshness info.
    """
    name_pattern = arguments.get("name_pattern")

    try:
        svc = get_service()
        packs = svc.list_packs(name_pattern)
        result = {
            "packs": [_omit_none(p) for p in packs],
            "total": len(packs),
        }
        return json.dumps(result)

    except Exception as e:
        logger.exception("list_packs error")
        return json.dumps({
            "error": True,
            "error_code": "INTERNAL_ERROR",
            "message": str(e),
            "packs": [],
            "total": 0,
        })
