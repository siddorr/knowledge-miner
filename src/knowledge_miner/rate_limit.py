from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import time

from fastapi import Depends, HTTPException, status

from .auth import require_api_key


@dataclass
class InMemoryRateLimiter:
    limit: int = 60
    window_seconds: int = 60
    _buckets: dict[str, deque[float]] = field(default_factory=dict)

    def check(self, key: str, *, now: float | None = None) -> bool:
        ts = now if now is not None else time.time()
        bucket = self._buckets.setdefault(key, deque())
        cutoff = ts - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(ts)
        return True


rate_limiter = InMemoryRateLimiter(limit=60, window_seconds=60)


def require_rate_limit(token: str = Depends(require_api_key)) -> None:
    if not rate_limiter.check(token):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")

