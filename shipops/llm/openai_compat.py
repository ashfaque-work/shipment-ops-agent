"""Groq / Gemini through their OpenAI-compatible endpoints, with automatic 429 fallback.

Both providers expose `/chat/completions` with OpenAI tool-calling semantics, so one
client implementation serves both. Order = [primary, other]; on HTTP 429 (or a provider
5xx / connection failure) the next provider is tried.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import openai
from openai import OpenAI

from shipops.config import Settings
from shipops.llm.base import LLMError, LLMResult, RateLimiter

log = logging.getLogger(__name__)


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str


def providers_from_settings(s: Settings, *, primary: str | None = None) -> list[Provider]:
    all_p = {
        "groq": Provider("groq", s.groq_base_url, s.groq_api_key, s.groq_model),
        "gemini": Provider("gemini", s.gemini_base_url, s.gemini_api_key, s.gemini_model),
    }
    first = primary or s.llm_provider
    order = [first] + [n for n in all_p if n != first] if first in all_p else list(all_p)
    return [all_p[n] for n in order if all_p[n].api_key]


def _normalize_message(msg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or None}
    calls = []
    for tc in msg.tool_calls or []:
        args = tc.function.arguments or "{}"
        try:
            json.loads(args)
        except json.JSONDecodeError:
            args = "{}"
        calls.append(
            {
                "id": tc.id or f"call_{uuid.uuid4().hex[:12]}",  # some providers omit ids
                "type": "function",
                "function": {"name": tc.function.name, "arguments": args},
            }
        )
    if calls:
        out["tool_calls"] = calls
    return out


class OpenAICompatLLM:
    def __init__(
        self,
        providers: list[Provider],
        *,
        timeout_s: float = 60.0,
        limiter: RateLimiter | None = None,
        temperature: float = 0.0,
        clients: dict[str, Any] | None = None,
        request_options: dict[str, Any] | None = None,
        rate_limit_retries: int = 0,
        rate_limit_wait_s: float = 20.0,
        sleep=time.sleep,
    ):
        if not providers:
            raise LLMError(
                "No LLM provider configured. Set GROQ_API_KEY and/or GEMINI_API_KEY in .env "
                "(or LLM_PROVIDER=offline for the deterministic baseline)."
            )
        self.providers = providers
        self.limiter = limiter or RateLimiter(0)
        self.temperature = temperature
        self.request_options = request_options or {}  # e.g. {"response_format": {...}}
        # When EVERY provider returns 429, wait and retry this many times (evals use this
        # to ride out per-minute token limits; the interactive API fails fast instead).
        self.rate_limit_retries = rate_limit_retries
        self.rate_limit_wait_s = rate_limit_wait_s
        self._sleep = sleep
        self._clients = clients or {
            p.name: OpenAI(api_key=p.api_key, base_url=p.base_url, timeout=timeout_s, max_retries=0)
            for p in providers
        }

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResult:
        for attempt in range(self.rate_limit_retries + 1):
            result, errors, wait = self._try_providers(messages, tools)
            if result is not None:
                return result
            all_429 = all("429" in e for e in errors)
            if not all_429 or attempt == self.rate_limit_retries:
                break
            delay = min(max(wait or self.rate_limit_wait_s, 1.0), 120.0)
            log.warning(
                "All providers rate-limited; waiting %.0fs (retry %d/%d)",
                delay,
                attempt + 1,
                self.rate_limit_retries,
            )
            self._sleep(delay)
        raise LLMError("All LLM providers failed: " + "; ".join(errors))

    def _try_providers(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[LLMResult | None, list[str], float | None]:
        errors: list[str] = []
        wait: float | None = None
        for i, p in enumerate(self.providers):
            self.limiter.acquire()
            t0 = time.perf_counter()
            try:
                kwargs: dict[str, Any] = {
                    "model": p.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    **self.request_options,
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                resp = self._clients[p.name].chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                errors.append(f"{p.name}: 429 rate limited")
                with contextlib.suppress(ValueError, AttributeError):
                    wait = float(exc.response.headers.get("retry-after", "")) or wait
                log.warning("LLM provider %s rate-limited; falling back (%s)", p.name, exc)
                continue
            except (openai.InternalServerError, openai.APIConnectionError) as exc:
                errors.append(f"{p.name}: {exc.__class__.__name__}")
                log.warning("LLM provider %s unavailable; falling back (%s)", p.name, exc)
                continue
            usage = {}
            if resp.usage:
                usage = {
                    "prompt_tokens": resp.usage.prompt_tokens or 0,
                    "completion_tokens": resp.usage.completion_tokens or 0,
                }
            result = LLMResult(
                message=_normalize_message(resp.choices[0].message),
                provider=p.name,
                model=p.model,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                fallback_from=self.providers[0].name if i > 0 else None,
                usage=usage,
            )
            return result, errors, wait
        return None, errors, wait
