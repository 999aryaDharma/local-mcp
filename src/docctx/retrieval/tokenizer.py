"""
FTS5 tokenizer — pre-processes text for camelCase and snake_case indexing.

Applied at both index time (via SQLite normalize_text function)
and query time (to expand query terms).
"""
from __future__ import annotations

import re


def normalize_for_index(text: str) -> str:
    """
    Expand camelCase and snake_case tokens so both whole tokens and their
    component parts are searchable via FTS5.

    Example:
        "useEffect"  → "useEffect use Effect"
        "fetch_data" → "fetch_data fetch data"
        "HTMLParser" → "HTMLParser HTML Parser"
    """
    if not text:
        return ""

    tokens = text.split()
    expanded = []

    for token in tokens:
        expanded.append(token)

        # Handle snake_case: split on underscores
        if "_" in token:
            parts = [p for p in token.split("_") if p]
            if len(parts) > 1:
                expanded.extend(parts)

        # Handle camelCase / PascalCase
        # Insert space before uppercase letters preceded by lowercase or digit
        camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", token)
        # Handle consecutive uppercase followed by lowercase (e.g., HTMLParser → HTML Parser)
        camel_split = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", camel_split)
        camel_parts = camel_split.split()

        if len(camel_parts) > 1:
            expanded.extend(camel_parts)

    # Deduplicate while preserving order
    seen = set()
    result = []
    for t in expanded:
        if t.lower() not in seen:
            seen.add(t.lower())
            result.append(t)

    return " ".join(result)


def normalize_query(query: str) -> str:
    """
    Normalize a query string for FTS5 searching.
    Expands camelCase/snake_case terms in the query.
    """
    return normalize_for_index(query)


def build_fts5_query(query: str) -> tuple[str, str]:
    """
    Build two FTS5 query variants:
    1. Phrase query: "term1 term2" (higher precision)
    2. OR query: term1 OR term2 (higher recall fallback)

    Returns (phrase_query, or_query).
    """
    # Normalize the query
    normalized = normalize_query(query)
    terms = normalized.split()

    if not terms:
        return ('""', '""')

    # Phase 1: quoted phrase for exact match
    phrase_query = f'"{query}"'

    # Phase 2: individual terms with OR
    # Escape special FTS5 chars
    escaped_terms = [_escape_fts5_term(t) for t in terms]
    or_query = " OR ".join(escaped_terms)

    return phrase_query, or_query


def _escape_fts5_term(term: str) -> str:
    """Escape special FTS5 characters in a term."""
    # FTS5 special chars: " ( ) * :
    # Wrap in quotes if contains special chars
    special = set('"()* :')
    if any(c in term for c in special):
        escaped = term.replace('"', '""')
        return f'"{escaped}"'
    return term
