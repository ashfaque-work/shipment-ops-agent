"""Application settings, loaded from environment / .env (never hard-code secrets)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM
    # groq | gemini (live, fallback to the other on 429) | offline (rule-based, no key)
    # | replay (answers from LLM_REPLAY_PATH, recorded with LLM_RECORD_PATH)
    llm_provider: Literal["groq", "gemini", "offline", "replay"] = "groq"
    llm_replay_path: str = ""
    llm_record_path: str = ""  # with a live provider: record every response here
    extractor: Literal["auto", "rules"] = "auto"  # rules = deterministic, no LLM calls
    groq_api_key: str = ""
    gemini_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_vision_model: str = "gemini-3.1-flash-lite"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    llm_timeout_s: float = 60.0
    llm_max_rpm: int = Field(default=0, description="Client-side throttle; 0 = unlimited")
    # Retries when every provider is rate-limited/overloaded (429/5xx). 0 = fail fast
    # (interactive API); recordings use a few retries to ride out spikes.
    llm_retries: int = 0

    # Carrier
    carrier: Literal["mock", "fedex"] = "mock"
    mock_carrier_url: str = "http://127.0.0.1:8001"  # not "localhost": avoids IPv6 lookup stalls
    carrier_timeout_s: float = 3.0
    carrier_max_retries: int = 2

    # API / demo
    demo_mode: bool = False
    rate_limit_per_minute: int = 20  # per client IP, demo mode only
    demo_daily_request_cap: int = 500  # all clients together, demo mode only (0 = off)
    trust_proxy: bool = False  # take client IP from X-Forwarded-For (behind Render etc.)
    max_upload_mb: float = 5.0
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
