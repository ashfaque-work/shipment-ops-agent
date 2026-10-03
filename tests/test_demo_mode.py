from __future__ import annotations

from fastapi.testclient import TestClient

from shipops.agent.service import AgentService
from shipops.agent.tools import ToolContext
from shipops.api.main import create_app
from shipops.api.ratelimit import DemoLimiter
from shipops.carriers import get_carrier
from shipops.carriers.mock import MockCarrierAdapter
from shipops.config import Settings
from shipops.llm.baseline import RuleBasedLLM


def _api(ctx: ToolContext, **settings) -> TestClient:
    s = Settings(llm_provider="offline", **settings)
    return TestClient(create_app(s, service=AgentService(RuleBasedLLM(), ctx)))


def test_demo_rate_limit_per_ip(ctx: ToolContext) -> None:
    api = _api(ctx, demo_mode=True, rate_limit_per_minute=2)
    body = {"message": "where is order 4512?"}
    assert api.post("/api/chat", json=body).status_code == 200
    assert api.post("/api/chat", json=body).status_code == 200
    r = api.post("/api/chat", json=body)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    assert "per minute" in r.json()["detail"]
    assert api.get("/api/health").status_code == 200  # cheap endpoints are not limited


def test_no_limit_outside_demo(ctx: ToolContext) -> None:
    api = _api(ctx, demo_mode=False, rate_limit_per_minute=1)
    for _ in range(3):
        assert api.post("/api/chat", json={"message": "hi"}).status_code == 200


def test_trusted_proxy_uses_last_forwarded_ip(ctx: ToolContext) -> None:
    api = _api(ctx, demo_mode=True, rate_limit_per_minute=1, trust_proxy=True)
    h1 = {"X-Forwarded-For": "6.6.6.6, 1.1.1.1"}  # first entry is spoofable
    h2 = {"X-Forwarded-For": "6.6.6.6, 2.2.2.2"}
    assert api.post("/api/chat", json={"message": "hi"}, headers=h1).status_code == 200
    assert api.post("/api/chat", json={"message": "hi"}, headers=h2).status_code == 200
    assert api.post("/api/chat", json={"message": "hi"}, headers=h1).status_code == 429


def test_daily_cap() -> None:
    t = [1_000_000.0]
    lim = DemoLimiter(per_minute=100, per_day_total=2, clock=lambda: t[0])
    assert lim.check("a")[0] and lim.check("b")[0]
    ok, retry, reason = lim.check("c")
    assert not ok and "budget" in reason and retry > 0
    t[0] += 86400
    assert lim.check("c")[0]  # resets the next day


def test_demo_mode_runs_mock_carrier_in_process() -> None:
    # Even with a (here unreachable) carrier URL and CARRIER=fedex, demo mode needs no
    # second service: the mock carrier runs inside the API process.
    s = Settings(carrier="fedex", demo_mode=True, mock_carrier_url="http://unreachable.invalid")
    carrier = get_carrier(s)
    assert isinstance(carrier, MockCarrierAdapter)
    assert carrier.track("MC100004512").status == "in_transit"


def test_inprocess_outage_is_bounded_by_carrier_timeout() -> None:
    import time

    from shipops.carriers.base import CarrierTimeout

    carrier = get_carrier(Settings(demo_mode=True, carrier_timeout_s=0.2))
    t0 = time.perf_counter()
    try:
        carrier.track("MC100000408")
    except CarrierTimeout:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected a carrier timeout")
    assert time.perf_counter() - t0 < 3  # ~2 x 0.2 s + backoff, not 2 x 10 s


def test_outside_demo_mode_uses_the_carrier_url() -> None:
    carrier = get_carrier(Settings(demo_mode=False, mock_carrier_url="http://127.0.0.1:8001"))
    assert str(carrier._client.base_url).startswith("http://127.0.0.1:8001")


def test_no_keys_falls_back_to_rule_based_with_a_notice() -> None:
    from shipops.api.main import resolve_llm_mode

    s, notice = resolve_llm_mode(Settings(llm_provider="groq", groq_api_key="", gemini_api_key=""))
    assert s.llm_provider == "offline" and "GROQ_API_KEY" in notice
    s, notice = resolve_llm_mode(Settings(llm_provider="groq", groq_api_key="k"))
    assert s.llm_provider == "groq" and notice is None
    s, notice = resolve_llm_mode(Settings(llm_provider="replay", llm_replay_path="x"))
    assert s.llm_provider == "replay" and notice is None


def test_fresh_clone_api_starts_without_keys(monkeypatch) -> None:
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    from shipops.api.main import create_app

    s = Settings(_env_file=None, mock_carrier_url="inprocess")
    with TestClient(create_app(s)) as api:
        h = api.get("/api/health").json()
        assert h["status"] == "ok" and h["llm_provider"] == "offline" and h["notice"]
        assert (
            "customs"
            in api.post("/api/chat", json={"message": "Where is order 4518?"}).json()["reply"]
        )
