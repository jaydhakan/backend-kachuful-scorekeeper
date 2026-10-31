"""ETags from the global data version, and a small per-worker response cache (plan §15.4). No Redis."""

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

TTL_S = 30.0
MAX_ENTRIES = 512


@dataclass(frozen=True)
class Cached[T]:
    """An ETag and the body, or `body=None` when the client's copy is current (304)."""

    etag: str
    body: T | None


def etag_for(data_version: int, *parts: object) -> str:
    """`"<data_version>-<hash of the request's parameters>"`."""
    digest = hashlib.sha256(repr(parts).encode()).hexdigest()[:16]
    return f'"{data_version}-{digest}"'


def etag_matches(if_none_match: str | None, etag: str) -> bool:
    """Whether If-None-Match names this ETag (weak forms count: nginx gzip weakens them)."""
    if not if_none_match:
        return False
    tags = {tag.strip().removeprefix("W/") for tag in if_none_match.split(",")}
    return etag in tags or "*" in tags


class TtlCache:
    """LRU with a time limit; entries are keyed by ETag, so a data change is a new key."""

    def __init__(self, ttl_s: float = TTL_S, max_entries: int = MAX_ENTRIES) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        """The cached value, if fresh."""
        item = self._items.get(key)
        if item is None:
            return None
        stored_at, value = item
        if time.monotonic() - stored_at > self.ttl_s:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key: str, value: Any) -> None:
        """Store a value, evicting the least recently used."""
        self._items[key] = (time.monotonic(), value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def clear(self) -> None:
        """Drop everything (tests)."""
        self._items.clear()


response_cache = TtlCache()
