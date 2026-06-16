"""
URL discovery — finds pages within scope via llms.txt, sitemap.xml, or BFS crawl.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from selectolax.parser import HTMLParser

from docctx.ingestion.scope import ScopeConfig, url_in_scope

logger = logging.getLogger(__name__)

LLMS_TXT_PATHS = ["/llms.txt", "/llms-full.txt"]
SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml"]


async def discover_urls(
    scope: ScopeConfig,
    client: httpx.AsyncClient,
    max_pages: int = 50,
    max_depth: int = 2,
) -> list[str]:
    """
    Discover URLs within scope. Tries in order:
    1. llms.txt (LLM-friendly doc listing)
    2. sitemap.xml
    3. BFS link crawl from entry_url
    """
    parsed = urlparse(scope.entry_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # 1. Try llms.txt
    urls = await _try_llms_txt(base, client, scope)
    if urls:
        logger.info("Discovered %d URLs via llms.txt", len(urls))
        return urls[:max_pages]

    # 2. Try sitemap.xml
    urls = await _try_sitemap(base, client, scope)
    if urls:
        logger.info("Discovered %d URLs via sitemap.xml", len(urls))
        return urls[:max_pages]

    # 3. BFS crawl
    logger.info("Falling back to BFS link crawl from %s", scope.entry_url)
    urls = await _bfs_crawl(scope, client, max_pages=max_pages, max_depth=max_depth)
    logger.info("Discovered %d URLs via BFS crawl", len(urls))
    return urls


async def _try_llms_txt(
    base: str, client: httpx.AsyncClient, scope: ScopeConfig
) -> list[str]:
    """Parse llms.txt for a list of doc URLs."""
    for path in LLMS_TXT_PATHS:
        url = base + path
        try:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                continue
            text = resp.text
            urls = _parse_llms_txt(text, base)
            in_scope = [u for u in urls if url_in_scope(u, scope)]
            if in_scope:
                return in_scope
        except Exception as e:
            logger.debug("llms.txt fetch failed for %s: %s", url, e)
    return []


def _parse_llms_txt(text: str, base: str) -> list[str]:
    """Extract URLs from llms.txt content (markdown link format or plain URLs)."""
    urls = []
    # Match markdown links: [text](url)
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", text):
        href = m.group(2).strip()
        if href.startswith("http"):
            urls.append(href)
        elif href.startswith("/"):
            urls.append(base + href)

    # Match plain URLs
    for m in re.finditer(r"https?://\S+", text):
        url = m.group(0).rstrip(".,;)")
        if url not in urls:
            urls.append(url)

    return urls


async def _try_sitemap(
    base: str, client: httpx.AsyncClient, scope: ScopeConfig
) -> list[str]:
    """Parse sitemap.xml for URLs."""
    for path in SITEMAP_PATHS:
        url = base + path
        try:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                continue
            urls = _parse_sitemap(resp.text)
            in_scope = [u for u in urls if url_in_scope(u, scope)]
            if in_scope:
                return in_scope
        except Exception as e:
            logger.debug("Sitemap fetch failed for %s: %s", url, e)
    return []


def _parse_sitemap(xml_text: str) -> list[str]:
    """Extract <loc> URLs from sitemap XML."""
    urls = []
    try:
        root = ElementTree.fromstring(xml_text)
        # Handle namespace
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"
        for loc in root.iter(f"{ns}loc"):
            if loc.text:
                urls.append(loc.text.strip())
    except ElementTree.ParseError as e:
        logger.debug("Sitemap parse error: %s", e)
    return urls


async def _bfs_crawl(
    scope: ScopeConfig,
    client: httpx.AsyncClient,
    max_pages: int = 50,
    max_depth: int = 2,
) -> list[str]:
    """BFS link crawl starting from entry_url, bounded by scope and depth."""
    visited: set[str] = set()
    result: list[str] = []
    queue: deque[tuple[str, int]] = deque([(scope.entry_url, 0)])

    while queue and len(result) < max_pages:
        url, depth = queue.popleft()
        clean_url = url.split("#")[0]

        if clean_url in visited:
            continue
        visited.add(clean_url)

        if not url_in_scope(clean_url, scope):
            continue

        result.append(clean_url)

        if depth >= max_depth:
            continue

        # Fetch and extract links
        try:
            resp = await client.get(url, follow_redirects=True)
            if resp.status_code != 200:
                continue
            links = _extract_links(resp.text, url)
            for link in links:
                clean_link = link.split("#")[0]
                if clean_link not in visited and url_in_scope(clean_link, scope):
                    queue.append((clean_link, depth + 1))
        except Exception as e:
            logger.debug("BFS fetch failed for %s: %s", url, e)

    return result


def _extract_links(html: str, base_url: str) -> list[str]:
    """Extract all <a href> links from HTML, resolved to absolute URLs."""
    parser = HTMLParser(html)
    links = []
    for node in parser.css("a[href]"):
        href = node.attributes.get("href", "")
        if not href or href.startswith(("mailto:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        links.append(absolute)
    return links
