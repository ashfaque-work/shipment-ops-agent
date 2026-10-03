"""LLM backends. `get_llm()` builds the one selected by LLM_PROVIDER."""

from __future__ import annotations

from shipops.config import Settings, get_settings
from shipops.llm.base import LLM, LLMError, LLMResult, RateLimiter


def get_llm(settings: Settings | None = None) -> LLM:
    s = settings or get_settings()
    if s.llm_provider == "offline":
        from shipops.llm.baseline import RuleBasedLLM

        return RuleBasedLLM()
    from shipops.llm.openai_compat import OpenAICompatLLM, providers_from_settings

    return OpenAICompatLLM(
        providers_from_settings(s),
        timeout_s=s.llm_timeout_s,
        limiter=RateLimiter(s.llm_max_rpm),
    )


__all__ = ["LLM", "LLMError", "LLMResult", "RateLimiter", "get_llm"]
