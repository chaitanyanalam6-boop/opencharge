"""In-process response cache and rate limiter.

Both live in memory, which is right for one API instance (the deployment plan). With several
instances, both would move to Redis; the interfaces here would stay the same.
"""

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any


class TTLCache:
    """A small least-recently-used cache whose entries expire after `ttl_seconds`.

    Data changes at most weekly (the ingest schedule), so minutes of staleness are harmless,
    and caching /api/search saves an LLM call per repeated question.
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int = 512,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            expires, value = hit
            if expires < self._clock():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = (self._clock() + self.ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class RateLimiter:
    """Sliding window: at most `limit` requests per `window_seconds` per client key."""

    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Record a request. Returns None if allowed, else seconds until the client may retry."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return round(hits[0] + self.window - now, 1)
            hits.append(now)
            return None
