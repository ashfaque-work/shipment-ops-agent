"""Shipping-document extraction and validation."""

from __future__ import annotations

from shipops.config import Settings, get_settings
from shipops.documents.store import DocumentStore, Extractor


def get_extractor(settings: Settings | None = None) -> Extractor:
    """Gemini Flash for extraction (text + vision); Groq as text-only fallback on 429.
    Without keys (or LLM_PROVIDER=offline) the rule-based parser is used."""
    s = settings or get_settings()
    no_live_llm = s.llm_provider in ("offline", "replay")
    if s.extractor == "rules" or no_live_llm or not (s.gemini_api_key or s.groq_api_key):
        return Extractor("rules")
    from shipops.llm.base import RateLimiter
    from shipops.llm.openai_compat import OpenAICompatLLM, Provider

    gemini = Provider("gemini", s.gemini_base_url, s.gemini_api_key, s.gemini_vision_model)
    groq = Provider("groq", s.groq_base_url, s.groq_api_key, s.groq_model)
    opts = {
        "request_options": {"response_format": {"type": "json_object"}},
        "timeout_s": s.llm_timeout_s,
        "limiter": RateLimiter(s.llm_max_rpm),
    }
    text_providers = [p for p in (gemini, groq) if p.api_key]
    text_llm = OpenAICompatLLM(text_providers, **opts)
    vision_llm = OpenAICompatLLM([gemini], **opts) if gemini.api_key else None
    return Extractor("auto", text_llm=text_llm, vision_llm=vision_llm)


__all__ = ["DocumentStore", "Extractor", "get_extractor"]
