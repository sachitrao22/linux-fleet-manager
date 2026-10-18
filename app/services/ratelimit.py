"""
Sliding-window rate limiter, kept in memory. Good enough for one server process (the
public demo); several processes would need a shared store such as Redis.
"""
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self.hits: Dict[str, Deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> bool:
        """Record one attempt; False if the key is over its limit."""
        now = time.monotonic()
        q = self.hits[key]
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self.hits) > 10_000:          # keep memory bounded
            for k in [k for k, v in self.hits.items() if not v][:5_000]:
                del self.hits[k]
        return True

    def check(self, request: Request, what: str) -> None:
        if not self.hit(client_ip(request)):
            raise HTTPException(status_code=429, detail=f"Too many {what} — please wait a minute and try again.")


def client_ip(request: Request) -> str:
    # Behind a proxy (Render, Fly…) uvicorn runs with --proxy-headers, so this is the real client
    return request.client.host if request.client else "unknown"
