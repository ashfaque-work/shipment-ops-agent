"""Demo-mode request limiting: per-client sliding window + a global daily cap.

In-memory, per process - right for a single free-tier instance. Behind a reverse proxy
(Render, Fly, ...) the real client IP is the LAST X-Forwarded-For entry (appended by the
proxy); earlier entries are client-supplied and spoofable.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from starlette.requests import Request


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


class DemoLimiter:
    def __init__(self, per_minute: int, per_day_total: int, *, clock=time.time):
        self.per_minute = per_minute
        self.per_day_total = per_day_total
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._day = self._today()
        self._day_count = 0
        self._lock = threading.Lock()

    def _today(self) -> int:
        return int(self._clock() // 86400)

    def check(self, key: str) -> tuple[bool, int, str]:
        """Returns (allowed, retry_after_seconds, reason)."""
        now = self._clock()
        with self._lock:
            if self._today() != self._day:
                self._day, self._day_count = self._today(), 0
            if self.per_day_total and self._day_count >= self.per_day_total:
                tomorrow = (self._day + 1) * 86400
                return (
                    False,
                    int(tomorrow - now) + 1,
                    (
                        "The demo has used today's request budget (it runs on free API tiers). "
                        "Please come back tomorrow, or run it locally with your own keys."
                    ),
                )
            q = self._hits[key]
            while q and now - q[0] >= 60:
                q.popleft()
            if self.per_minute and len(q) >= self.per_minute:
                wait = int(60 - (now - q[0])) + 1
                return (
                    False,
                    wait,
                    (f"Demo limit: {self.per_minute} requests per minute. Try again in {wait} s."),
                )
            q.append(now)
            self._day_count += 1
            if len(self._hits) > 10_000:  # bound memory under abuse
                for k in [k for k, v in self._hits.items() if not v][:5_000]:
                    del self._hits[k]
            return True, 0, ""
