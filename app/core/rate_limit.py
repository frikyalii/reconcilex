"""Thread-safe sliding-window rate limiter for expensive ReconcileX operations."""

from __future__ import annotations

from collections import defaultdict, deque
import threading
import time
from typing import Callable, Deque, Dict

from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter keyed by client identifier and route bucket."""

    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def check_or_raise(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= self.max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"Rate limit exceeded ({self.max_requests} requests per "
                        f"{int(self.window_seconds)}s). Please retry later."
                    ),
                )
            dq.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


expensive_ops_limiter = SlidingWindowRateLimiter(max_requests=100, window_seconds=60.0)


def rate_limit_dependency(bucket: str) -> Callable[[Request], None]:
    """FastAPI dependency enforcing rate limits per caller IP/API key and route bucket."""

    def _dep(request: Request) -> None:
        caller = (
            request.headers.get("X-API-Key")
            or (request.client.host if request.client else "local")
        )
        expensive_ops_limiter.check_or_raise(f"{bucket}:{caller}")

    return _dep
