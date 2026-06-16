from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional


@dataclass
class CacheEntry:
    value: object
    created_at: float = field(default_factory=time.monotonic)
    hits: int = 0


class QueryCache:
    """
    Simple LRU-ish cache untuk search results.
    """

    def __init__(self, max_size: int = 100, ttl_seconds: float = 300.0):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._store: dict[str, CacheEntry] = {}
        self._lock = Lock()

    def _make_key(self, query: str, pack: Optional[str], limit: int, min_confidence: str) -> str:
        return f"{query}|{pack or '*'}|{limit}|{min_confidence}"

    def get(self, query: str, pack: Optional[str], limit: int, min_confidence: str):
        key = self._make_key(query, pack, limit, min_confidence)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() - entry.created_at > self._ttl:
                del self._store[key]
                return None
            entry.hits += 1
            return entry.value

    def set(self, query: str, pack: Optional[str], limit: int, min_confidence: str, value: object) -> None:
        key = self._make_key(query, pack, limit, min_confidence)
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                oldest_key = next(iter(self._store))
                del self._store[oldest_key]
            self._store[key] = CacheEntry(value=value)

    def invalidate(self, pack_name: Optional[str] = None) -> int:
        with self._lock:
            if pack_name is None:
                count = len(self._store)
                self._store.clear()
                return count

            to_delete = []
            for key in self._store:
                parts = key.split("|")
                if len(parts) >= 2:
                    pack_filter = parts[1]
                    if pack_filter == "*" or pack_filter == pack_name or pack_name.startswith(pack_filter.rstrip("%")):
                        to_delete.append(key)

            for k in to_delete:
                del self._store[k]
            return len(to_delete)

    def stats(self) -> dict:
        with self._lock:
            total_hits = sum(e.hits for e in self._store.values())
            return {
                "size": len(self._store),
                "max_size": self._max_size,
                "ttl_seconds": self._ttl,
                "total_hits": total_hits,
            }
