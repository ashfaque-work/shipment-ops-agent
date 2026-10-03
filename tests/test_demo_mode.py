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


def test_demo_forces_mock_and_inprocess_carrier() -> None:
    carrier = get_carrier(Settings(carrier="fedex", demo_mode=True, mock_carrier_url="inprocess"))
    assert isinstance(carrier, MockCarrierAdapter)
    assert carrier.track("MC100004512").status == "in_transit"
