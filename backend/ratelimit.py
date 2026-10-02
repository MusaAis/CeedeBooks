"""Tiny in-memory sliding-window rate limiter.

Per-process state: run uvicorn with a single worker (see README) or the limits multiply.
"""
import time
from collections import defaultdict, deque
from typing import Optional

_SWEEP_AT = 5000        # bucket count that triggers cleanup
_STALE_AFTER = 3600.0   # seconds of silence before a bucket is dropped


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict = defaultdict(deque)

    def _trim(self, bucket: str, window: float, now: float) -> deque:
        hits = self._hits[bucket]
        cutoff = now - window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def allow(self, bucket: str, limit: int, window: float, now: Optional[float] = None) -> bool:
        """Record a hit and return True if the bucket is still within `limit` hits per `window` seconds."""
        now = time.monotonic() if now is None else now
        if len(self._hits) > _SWEEP_AT:
            self._sweep(now)
        hits = self._trim(bucket, window, now)
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def blocked(self, bucket: str, limit: int, window: float, now: Optional[float] = None) -> bool:
        """True if the bucket is already at its limit. Records nothing."""
        now = time.monotonic() if now is None else now
        if bucket not in self._hits:
            return False
        return len(self._trim(bucket, window, now)) >= limit

    def _sweep(self, now: float) -> None:
        for bucket in [b for b, h in self._hits.items() if not h or h[-1] <= now - _STALE_AFTER]:
            del self._hits[bucket]

    def reset(self) -> None:
        self._hits.clear()
