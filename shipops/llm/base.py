"""LLM abstraction: every backend returns an OpenAI-format assistant message dict."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    """All providers failed or returned something unusable."""


@dataclass
class LLMResult:
    message: dict[str, Any]  # {"role": "assistant", "content": str|None, "tool_calls": [...]}
    provider: str
    model: str
    latency_ms: int = 0
    fallback_from: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return self.message.get("tool_calls") or []


class LLM(Protocol):
    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResult: ...


class RateLimiter:
    """Thread-safe sliding-window limiter: at most `rpm` acquisitions per 60 s (0 = off)."""

    def __init__(self, rpm: int, *, clock=time.monotonic, sleep=time.sleep):
        self.rpm = rpm
        self._clock, self._sleep = clock, sleep
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Blocks until a slot is free; returns seconds waited."""
        if self.rpm <= 0:
            return 0.0
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                while self._hits and now - self._hits[0] >= 60:
                    self._hits.popleft()
                if len(self._hits) < self.rpm:
                    self._hits.append(now)
                    return waited
                delay = 60 - (now - self._hits[0]) + 0.05
            self._sleep(delay)
            waited += delay
