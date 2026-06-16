"""
HTTP fetcher with rate limiting, disk caching, and robots.txt support.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from docctx.exceptions import FetchError
from docctx.paths import get_cache_dir

logger = logging.getLogger(__name__)


class TokenBucket:
    """Simple token bucket for rate limiting."""

    def __init__(self, rate_rps: float):
        self.rate = rate_rps
        self._tokens = rate_rps
        self._last = time.monotonic()

    async def acquire(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._tokens = min(self.rate, self._tokens + elapsed * self.rate)
        self._last = now

        if self._tokens >= 1.0:
            self._tokens -= 1.0
        else:
            wait = (1.0 - self._tokens) / self.rate
            await asyncio.sleep(wait)
            self._tokens = 0.0


class RobotsCache:
    """Cache for robots.txt parsers per domain."""

    def __init__(self):
        self._cache: dict[str, RobotFileParser] = {}

    async def is_allowed(
        self, url: str, user_agent: str, client: httpx.AsyncClient
    ) -> bool:
        parsed = urlparse(url)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        if domain not in self._cache:
            robots_url = f"{domain}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            try:
                resp = await client.get(robots_url, follow_redirects=True)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    # No robots.txt — allow everything
                    rp.parse([])
            except Exception:
                rp.parse([])
            self._cache[domain] = rp

        return self._cache[domain].can_fetch(user_agent, url)


class Fetcher:
    """
    Async HTTP fetcher with:
    - Token bucket rate limiting
    - SHA256-keyed disk cache (invalidated only on refresh)
    - robots.txt compliance
    """

    def __init__(
        self,
        rate_limit_rps: float = 1.0,
        timeout: int = 30,
        user_agent: str = "docctx/1.0",
        cache_enabled: bool = True,
        respect_robots: bool = True,
    ):
        self.bucket = TokenBucket(rate_limit_rps)
        self.timeout = timeout
        self.user_agent = user_agent
        self.cache_enabled = cache_enabled
        self.respect_robots = respect_robots
        self.robots_cache = RobotsCache()
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self.user_agent},
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    def _cache_path(self, url: str) -> Path:
        key = hashlib.sha256(url.encode()).hexdigest()
        return get_cache_dir() / f"{key}.html"

    def _read_cache(self, url: str) -> Optional[str]:
        if not self.cache_enabled:
            return None
        path = self._cache_path(url)
        if path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return None
        return None

    def _write_cache(self, url: str, html: str) -> None:
        if not self.cache_enabled:
            return
        path = self._cache_path(url)
        try:
            path.write_text(html, encoding="utf-8")
        except OSError as e:
            logger.warning("Failed to write cache for %s: %s", url, e)

    def invalidate_cache(self, url: str) -> None:
        """Remove cached HTML for a URL (called on refresh)."""
        path = self._cache_path(url)
        if path.exists():
            path.unlink()

    async def fetch(self, url: str) -> str:
        """
        Fetch HTML content for a URL.
        Returns cached content if available, otherwise fetches and caches.
        Raises FetchError on failure.
        """
        # Check cache first
        cached = self._read_cache(url)
        if cached is not None:
            logger.debug("Cache hit: %s", url)
            return cached

        # Check robots.txt
        if self.respect_robots:
            allowed = await self.robots_cache.is_allowed(url, self.user_agent, self.client)
            if not allowed:
                raise FetchError(
                    f"robots.txt disallows fetching {url}",
                    hint="Use --no-robots to override (not recommended).",
                )

        # Rate limit
        await self.bucket.acquire()

        # Fetch
        try:
            logger.debug("Fetching: %s", url)
            resp = await self.client.get(url)
            resp.raise_for_status()
            html = resp.text
            self._write_cache(url, html)
            return html
        except httpx.HTTPStatusError as e:
            raise FetchError(
                f"HTTP {e.response.status_code} fetching {url}",
                hint=f"Check if the URL is accessible: {url}",
            ) from e
        except httpx.RequestError as e:
            raise FetchError(
                f"Network error fetching {url}: {e}",
                hint="Check your internet connection or the URL.",
            ) from e
