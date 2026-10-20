from __future__ import annotations

from types import SimpleNamespace

import httpx
import openai
import pytest

from shipops.config import Settings
from shipops.llm.base import LLMError, RateLimiter
from shipops.llm.openai_compat import OpenAICompatLLM, Provider, providers_from_settings


def _resp(content=None, tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _rate_limit_error() -> openai.RateLimitError:
    req = httpx.Request("POST", "http://x/chat/completions")
    return openai.RateLimitError(
        "rate limited", response=httpx.Response(429, request=req), body=None
    )


class FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


P = [Provider("groq", "u", "k1", "llama"), Provider("gemini", "u", "k2", "flash")]


def test_primary_success() -> None:
    groq, gem = FakeClient([_resp("hi")]), FakeClient([])
    llm = OpenAICompatLLM(P, clients={"groq": groq, "gemini": gem})
    res = llm.complete([{"role": "user", "content": "x"}], [])
    assert res.provider == "groq" and res.fallback_from is None
    assert res.message == {"role": "assistant", "content": "hi"}
    assert not gem.calls


def test_falls_back_on_429() -> None:
    groq = FakeClient([_rate_limit_error()])
    tc = SimpleNamespace(
        id="", function=SimpleNamespace(name="track_shipment", arguments='{"order_id": "4512"}')
    )
    gem = FakeClient([_resp(None, [tc])])
    llm = OpenAICompatLLM(P, clients={"groq": groq, "gemini": gem})
    res = llm.complete([{"role": "user", "content": "x"}], [{"type": "function"}])
    assert res.provider == "gemini" and res.fallback_from == "groq"
    call = res.message["tool_calls"][0]
    assert call["id"].startswith("call_")  # missing ids are synthesised
    assert call["function"]["name"] == "track_shipment"
    assert gem.calls[0]["model"] == "flash"


def test_all_rate_limited_raises() -> None:
    llm = OpenAICompatLLM(
        P,
        clients={
            "groq": FakeClient([_rate_limit_error()]),
            "gemini": FakeClient([_rate_limit_error()]),
        },
    )
    with pytest.raises(LLMError, match="429"):
        llm.complete([], [])


def test_malformed_arguments_sanitised() -> None:
    tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="get_order", arguments="{bad"))
    llm = OpenAICompatLLM(P[:1], clients={"groq": FakeClient([_resp(None, [tc])])})
    out = llm.complete([], [])
    assert out.message["tool_calls"][0]["function"]["arguments"] == "{}"


def test_no_providers_configured() -> None:
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        OpenAICompatLLM([])


def test_provider_order_from_settings() -> None:
    s = Settings(llm_provider="gemini", groq_api_key="a", gemini_api_key="b")
    assert [p.name for p in providers_from_settings(s)] == ["gemini", "groq"]
    s = Settings(llm_provider="groq", groq_api_key="", gemini_api_key="b")
    assert [p.name for p in providers_from_settings(s)] == ["gemini"]


def test_rate_limiter_blocks_when_full() -> None:
    t = [0.0]
    slept: list[float] = []

    def sleep(d: float) -> None:
        slept.append(d)
        t[0] += d

    lim = RateLimiter(2, clock=lambda: t[0], sleep=sleep)
    assert lim.acquire() == 0 and lim.acquire() == 0
    waited = lim.acquire()
    assert waited > 59 and slept


def test_waits_and_retries_when_all_rate_limited() -> None:
    slept: list[float] = []
    groq = FakeClient([_rate_limit_error(), _resp("ok")])
    llm = OpenAICompatLLM(
        P[:1],
        clients={"groq": groq},
        rate_limit_retries=2,
        rate_limit_wait_s=7,
        sleep=slept.append,
    )
    assert llm.complete([], []).message["content"] == "ok"
    assert slept == [7]


def test_daily_quota_raises_quota_exhausted() -> None:
    from shipops.llm.base import QuotaExhausted

    req = httpx.Request("POST", "http://x/chat/completions")
    err = openai.RateLimitError(
        "Quota exceeded for metric ... limit: 20. Please retry in 17h29m50.9s.",
        response=httpx.Response(429, request=req),
        body=None,
    )
    slept: list[float] = []
    llm = OpenAICompatLLM(
        P[:1], clients={"groq": FakeClient([err])}, rate_limit_retries=5, sleep=slept.append
    )
    with pytest.raises(QuotaExhausted) as info:
        llm.complete([], [])
    assert info.value.retry_after_s == pytest.approx(17 * 3600 + 29 * 60 + 50.9)
    assert slept == []  # no pointless waiting


def _status_error(cls: type[openai.APIStatusError], code: int) -> openai.APIStatusError:
    req = httpx.Request("POST", "http://x/chat/completions")
    return cls("rejected", response=httpx.Response(code, request=req), body=None)


@pytest.mark.parametrize(
    "error",
    [
        _status_error(openai.BadRequestError, 400),  # Groq "tool_use_failed"
        _status_error(openai.AuthenticationError, 401),  # revoked key
        _status_error(openai.NotFoundError, 404),  # retired model
    ],
)
def test_falls_back_on_provider_rejection(error: openai.APIStatusError) -> None:
    gem = FakeClient([_resp("from gemini")])
    llm = OpenAICompatLLM(P, clients={"groq": FakeClient([error]), "gemini": gem})
    res = llm.complete([{"role": "user", "content": "x"}], [])
    assert res.provider == "gemini" and res.message["content"] == "from gemini"


def test_rejection_everywhere_is_an_llm_error() -> None:
    """Must be LLMError (the agent answers politely), never a raw SDK error (HTTP 500)."""
    bad = _status_error(openai.BadRequestError, 400)
    llm = OpenAICompatLLM(P, clients={"groq": FakeClient([bad]), "gemini": FakeClient([bad])})
    with pytest.raises(LLMError, match="HTTP 400"):
        llm.complete([], [])


def test_empty_choices_fall_back() -> None:
    empty = SimpleNamespace(choices=[], usage=None)
    gem = FakeClient([_resp("ok")])
    llm = OpenAICompatLLM(P, clients={"groq": FakeClient([empty]), "gemini": gem})
    assert llm.complete([], []).provider == "gemini"
