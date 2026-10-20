"""Groq / Gemini through their OpenAI-compatible endpoints, with automatic 429 fallback.

Both providers expose `/chat/completions` with OpenAI tool-calling semantics, so one
client implementation serves both. Order = [primary, other]; on HTTP 429 (or a provider
5xx / connection failure) the next provider is tried.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import openai
from openai import OpenAI

from shipops.config import Settings
from shipops.llm.base import LLMError, LLMResult, QuotaExhausted, RateLimiter

MAX_WAIT_S = 120.0  # a longer retry-after means a daily quota: give up instead of waiting
_RETRY_IN = re.compile(r"retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", re.I)


def _retry_after(exc: Exception) -> float | None:
    """Seconds to wait, from the Retry-After header or Gemini's 'Please retry in 17h29m50s'."""
    with contextlib.suppress(ValueError, AttributeError, TypeError):
        header = exc.response.headers.get("retry-after")  # type: ignore[attr-defined]
        if header:
            return float(header)
    m = _RETRY_IN.search(str(exc))
    if m and any(m.groups()):
        h, mi, se = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mi * 60 + se
    return None


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
        call: dict[str, Any] = {
            "id": tc.id or f"call_{uuid.uuid4().hex[:12]}",  # some providers omit ids
            "type": "function",
            "function": {"name": tc.function.name, "arguments": args},
        }
        # Gemini 3 returns a "thought signature" that must be echoed back on later turns.
        extra = getattr(tc, "model_extra", None) or {}
        if extra.get("extra_content"):
            call["extra_content"] = extra["extra_content"]
        calls.append(call)
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
        max_wait_s: float = MAX_WAIT_S,
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
        # When EVERY provider fails transiently (429/5xx), wait and retry this many times
        # (evals ride out per-minute token limits this way; the interactive API fails fast).
        self.rate_limit_retries = rate_limit_retries
        self.rate_limit_wait_s = rate_limit_wait_s
        self.max_wait_s = max_wait_s  # longer retry-after => treat as exhausted quota
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
            if all_429 and wait is not None and wait > self.max_wait_s:
                raise QuotaExhausted(
                    "All LLM providers are out of quota (retry in "
                    f"{wait / 3600:.1f} h): " + "; ".join(errors),
                    wait,
                )
            # 429 (quota) and 5xx "high demand" are transient; anything else is not.
            transient = all("429" in e or "InternalServerError" in e for e in errors)
            if not transient or attempt == self.rate_limit_retries:
                break
            delay = min(max(wait or self.rate_limit_wait_s, 1.0), self.max_wait_s)
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
                w = _retry_after(exc)
                if w is not None:  # across providers, keep the shortest wait
                    wait = w if wait is None else min(wait, w)
                log.warning("LLM provider %s rate-limited; falling back (%s)", p.name, exc)
                continue
            except (openai.InternalServerError, openai.APIConnectionError) as exc:
                errors.append(f"{p.name}: {exc.__class__.__name__}")
                log.warning("LLM provider %s unavailable; falling back (%s)", p.name, exc)
                continue
            except openai.APIStatusError as exc:
                # 400 (e.g. Groq "tool_use_failed" on a malformed tool call), 401/403 (bad
                # key), 404 (retired model): not transient, but the other provider may work.
                errors.append(f"{p.name}: HTTP {exc.status_code} {exc.__class__.__name__}")
                log.warning("LLM provider %s rejected the request; falling back (%s)", p.name, exc)
                continue
            if not resp.choices:
                errors.append(f"{p.name}: empty response")
                log.warning("LLM provider %s returned no choices; falling back", p.name)
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
