"""
Scope resolution — determines crawl boundary from URL and user input.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from docctx.exceptions import ScopeAmbiguousError


VALID_SCOPES = ("page-only", "siblings", "subtree", "site")


@dataclass
class ScopeConfig:
    rule: str        # one of VALID_SCOPES
    entry_url: str
    base_prefix: str  # URL prefix that bounds the crawl


def _url_depth(url: str) -> int:
    """Return the path depth of a URL (number of meaningful path segments)."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]
    return len(segments)


def _normalize_url(url: str) -> str:
    """Ensure URL has a scheme."""
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def resolve_scope(url: str, explicit_scope: str | None) -> ScopeConfig:
    """
    Determine the crawl scope config from an entry URL and optional explicit scope.

    Rules:
    - Deep URL (path depth > 1): defaults to 'siblings' if no explicit scope.
    - Root URL (path depth ≤ 1): requires explicit scope; raises ScopeAmbiguousError otherwise.
    - 'site' scope always requires explicit flag (never inferred).
    """
    url = _normalize_url(url)
    parsed = urlparse(url)
    depth = _url_depth(url)

    if explicit_scope is not None:
        if explicit_scope not in VALID_SCOPES:
            raise ValueError(
                f"Invalid scope '{explicit_scope}'. Must be one of: {', '.join(VALID_SCOPES)}"
            )
        rule = explicit_scope
    else:
        # Infer scope from URL depth
        if depth <= 1:
            raise ScopeAmbiguousError(
                f"URL '{url}' points to a root/domain — scope is ambiguous.\n"
                f"Use --scope to specify one of: {', '.join(VALID_SCOPES)}",
                hint="For a full site crawl use --scope site. "
                "For docs under a path use --scope subtree.",
            )
        # Deep URL → default to siblings
        rule = "siblings"

    base_prefix = _compute_base_prefix(url, rule, parsed)
    return ScopeConfig(rule=rule, entry_url=url, base_prefix=base_prefix)


def _compute_base_prefix(url: str, rule: str, parsed) -> str:
    """Compute the URL prefix that defines the crawl boundary."""
    scheme = parsed.scheme
    netloc = parsed.netloc
    path = parsed.path.rstrip("/")

    if rule == "page-only":
        # Only the exact entry URL
        return url

    elif rule == "siblings":
        # Parent directory of the entry URL path
        # e.g. /docs/react/hooks → /docs/react/
        parts = path.split("/")
        # Remove last segment to get parent
        parent_parts = parts[:-1] if len(parts) > 1 else parts
        parent_path = "/".join(parent_parts)
        return f"{scheme}://{netloc}{parent_path}/"

    elif rule == "subtree":
        # Entry URL path itself as prefix
        return f"{scheme}://{netloc}{path}/"

    elif rule == "site":
        # Whole domain
        return f"{scheme}://{netloc}/"

    return url


def url_in_scope(url: str, scope: ScopeConfig) -> bool:
    """
    Return True if the given URL is within the crawl scope.
    Always enforces same-domain.
    """
    entry_parsed = urlparse(scope.entry_url)
    url_parsed = urlparse(url)

    # Same domain only
    if url_parsed.netloc != entry_parsed.netloc:
        return False

    # Fragment-only URLs are not real pages
    if not url_parsed.scheme.startswith("http"):
        return False

    if scope.rule == "page-only":
        # Strip fragments for comparison
        return url.split("#")[0] == scope.entry_url.split("#")[0]

    elif scope.rule in ("siblings", "subtree", "site"):
        normalized = url.split("#")[0]  # strip fragments
        return normalized.startswith(scope.base_prefix) or normalized == scope.entry_url.rstrip("/")

    return False
