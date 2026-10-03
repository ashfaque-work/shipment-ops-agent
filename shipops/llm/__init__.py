"""LLM backends. `get_llm()` builds the one selected by LLM_PROVIDER."""

from __future__ import annotations

from shipops.config import Settings, get_settings
from shipops.llm.base import LLM, LLMError, LLMResult, RateLimiter


def get_llm(settings: Settings | None = None) -> LLM:
    s = settings or get_settings()
    if s.llm_provider == "offline":
        from shipops.llm.baseline import RuleBasedLLM

        return RuleBasedLLM()
    if s.llm_provider == "replay":
        from shipops.llm.replay import ReplayLLM

        if not s.llm_replay_path:
            raise LLMError("LLM_PROVIDER=replay needs LLM_REPLAY_PATH")
        return ReplayLLM(s.llm_replay_path)
    from shipops.llm.openai_compat import OpenAICompatLLM, providers_from_settings

    llm: LLM = OpenAICompatLLM(
        providers_from_settings(s),
        timeout_s=s.llm_timeout_s,
        limiter=RateLimiter(s.llm_max_rpm),
        rate_limit_retries=s.llm_retries,
    )
    if s.llm_record_path:
        from shipops.llm.replay import RecordingLLM

        llm = RecordingLLM(llm, s.llm_record_path)
    return llm


__all__ = ["LLM", "LLMError", "LLMResult", "RateLimiter", "get_llm"]
